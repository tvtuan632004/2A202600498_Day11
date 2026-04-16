"""
Lab 11 — Part 2B: Output Guardrails
  TODO 6: Content filter (PII, secrets)
  TODO 7: LLM-as-Judge
  TODO 8: Output Guardrail Plugin (ADK)
"""
import re
import asyncio

from google import genai
from google.genai import types
from google.adk.plugins import base_plugin
from google.adk.agents.invocation_context import InvocationContext


# ============================================================
# TODO 6: Content filter
# ============================================================

def contains_bad_content(text: str) -> bool:
    """Detect harmful content in response."""
    bad_words = ["hack", "bomb", "kill", "exploit"]
    text_lower = text.lower()
    return any(word in text_lower for word in bad_words)

def contains_sensitive_info(text: str) -> bool:
    """Detect leakage of passwords, API keys, internal systems, database info."""
    sensitive_patterns = [
        r"password\s*[:=]\s*\S+",
        r"sk-[a-zA-Z0-9-]+",
        r"internal",
        r"database",
        r"db\."
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in sensitive_patterns)

def contains_pii(text: str) -> bool:
    """Detect phone numbers and emails."""
    pii_patterns = [
        r"\b\d{9,11}\b",
        r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    ]
    return any(re.search(p, text) for p in pii_patterns)

def redact_sensitive(text: str) -> str:
    """Replace sensitive data with [REDACTED]."""
    patterns_to_redact = [
        r"\b\d{9,11}\b",
        r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        r"(password\s*[:=]\s*\S+)",
        r"(sk-[a-zA-Z0-9-]+)"
    ]
    redacted_text = text
    for pattern in patterns_to_redact:
        redacted_text = re.sub(pattern, "[REDACTED]", redacted_text, flags=re.IGNORECASE)
    return redacted_text

def check_output(text: str) -> tuple[bool, str]:
    """Block harmful or sensitive outputs, redact PII if needed.
    Returns: (is_safe, processed_message)
    """
    if contains_bad_content(text):
        return False, "Blocked: Harmful or unsafe content detected."
        
    if contains_sensitive_info(text):
        return False, "Blocked: Sensitive information leakage detected."
        
    if contains_pii(text):
        # We allow it, but redact it
        return True, redact_sensitive(text)
        
    return True, text


# ============================================================
# TODO 7: LLM-as-Judge
# ============================================================

_judge_client = None

def _init_judge():
    """Initialize the AI judge client."""
    global _judge_client
    if _judge_client is None:
        _judge_client = genai.Client()

def llm_as_judge(text: str) -> bool:
    """Use Gemini to evaluate if the response text is safe.
    
    Returns:
        True if safe, False if unsafe
    """
    if _judge_client is None:
        _init_judge()
        
    JUDGE_PROMPT = f"""You are a security AI judge. Evaluate the following text.
If it contains passwords, API keys, internal system architectures, database URLs or any malicious content, respond exactly with 'UNSAFE'.
Otherwise, respond exactly with 'SAFE'.

Text to evaluate: {text}"""

    try:
        response = _judge_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=JUDGE_PROMPT,
        )
        verdict = response.text.strip().upper()
        return verdict == "SAFE"
    except Exception as e:
        print(f"LLM Judge error: {e}")
        # Default to False (fail-closed) if judge fails
        return False


# ============================================================
# TODO 8: Output Guardrail Plugin
# ============================================================

class OutputGuardrailPlugin(base_plugin.BasePlugin):
    """Plugin that processes model outputs before returning to user."""
    
    def __init__(self, use_llm_judge=False):
        super().__init__(name="output_guardrail")
        self.use_llm_judge = use_llm_judge
        self.blocked_count = 0

    def _extract_text(self, content: types.Content) -> str:
        """Extract plain text from a Content object."""
        text = ""
        if content and content.parts:
            for part in content.parts:
                if hasattr(part, "text") and part.text:
                    text += part.text
        return text

    async def on_model_response_callback(
        self,
        *,
        invocation_context: InvocationContext,
        model_response: types.Content,
    ) -> types.Content | None:
        """Process model response before it is sent to the user.
        
        Returns:
            None if message is perfectly fine (let pass).
            types.Content if we want to modify/block it.
        """
        text = self._extract_text(model_response)
        
        # 1. Rule-based check
        is_safe, processed_str = check_output(text)
        
        # 2. LLM Judge check (if enabled)
        if is_safe and self.use_llm_judge:
            llm_safe = llm_as_judge(processed_str)
            if not llm_safe:
                is_safe = False
                processed_str = "Blocked: Response was rejected by AI Safety Judge."
                
        if not is_safe:
            self.blocked_count += 1
            return types.Content(
                role="model",
                parts=[types.Part.from_text(text=processed_str)]
            )
            
        # If safe but modified (redacted PII)
        if is_safe and processed_str != text:
             return types.Content(
                role="model",
                parts=[types.Part.from_text(text=processed_str)]
            )

        # Nothing changed
        return None

# ============================================================
# Quick test
# ============================================================

def test_content_filter():
    """Test functionality of output guardrails."""
    print("Testing output guardrails (content filter)...")
    
    test_cases = [
        "Welcome to Vinbank! How can I help?",
        "The system internal database is at db.vinbank.internal:5432.",
        "Your new password is: admin123",
        "Please call 0123456789 for more details.",
        "How to hack a bank?"
    ]
    
    for i, text in enumerate(test_cases, 1):
        safe, result = check_output(text)
        status = "PASSED/REDACTED" if safe else "BLOCKED"
        print(f"  {i}. [{status}] Original: {text}")
        print(f"     -> Result: {result}")

if __name__ == "__main__":
    test_content_filter()
