"""
Local LLM client using Ollama for text formatting, auditing, and auxiliary tasks.

Uses qwen3.5:4b (or configurable model) for:
- Formatting raw output from main LLM
- Auditing prompts and responses for quality/safety
- Performing auxiliary tasks while main model works
- Summarizing long contexts
"""

import json
import time
from threading import Lock
from typing import Optional, List, Dict, Any, Generator
from openai import OpenAI

from config.config import LOCAL_BASE_URL, LOCAL_API_KEY


class LocalLLMClient:
    """Client for local Ollama LLM (qwen3.5:4b)."""

    def __init__(self, model: str = "qwen3.5:4b", base_url: str = None):
        """
        Initialize the local LLM client.

        Args:
            model: Model name to use (default: qwen3.5:4b)
            base_url: Ollama API base URL (default: http://localhost:11434/v1)
        """
        self.model = model
        self.base_url = base_url or LOCAL_BASE_URL
        self.client = OpenAI(
            base_url=self.base_url,
            api_key=LOCAL_API_KEY
        )
        self._available = None
        self._warmup_lock = Lock()
        self._last_warmup = 0.0
        self._warmup_ttl_seconds = 120.0

    def is_available(self) -> bool:
        """Check if the local LLM is available."""
        if self._available is not None:
            return self._available

        try:
            models = self.client.models.list()
            model_names = [m.id for m in models]
            self._available = any(self.model in m for m in model_names)
            return self._available
        except Exception:
            self._available = False
            return False

    def chat(self, messages: List[Dict],
             temperature: float = 0.3,
             max_tokens: int = 2048,
             stream: bool = False,
             think: Optional[bool] = None,
             response_format: Optional[Dict[str, Any]] = None) -> Any:
        """
        Send a chat request to the local LLM.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature
            max_tokens: Maximum tokens in response
            stream: Whether to stream the response

        Returns:
            Response object or generator if streaming
        """
        request_kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if think is False:
            request_kwargs["reasoning_effort"] = "none"
        elif think is True:
            request_kwargs["reasoning_effort"] = "high"
        if response_format is not None:
            request_kwargs["response_format"] = response_format

        return self.client.chat.completions.create(
            **request_kwargs
        )

    def chat_complete(self, messages: List[Dict],
                      temperature: float = 0.3,
                      max_tokens: int = 2048,
                      think: Optional[bool] = None,
                      response_format: Optional[Dict[str, Any]] = None) -> str:
        """
        Send a chat request and return the complete response text.

        Args:
            messages: List of message dicts
            temperature: Sampling temperature
            max_tokens: Maximum tokens

        Returns:
            Response text as string
        """
        response = self.chat(
            messages,
            temperature,
            max_tokens,
            stream=False,
            think=think,
            response_format=response_format,
        )
        return response.choices[0].message.content

    def warmup(self, force: bool = False) -> bool:
        """Preload the local model with a tiny request so later calls start faster."""
        now = time.monotonic()
        if not force and self._last_warmup and (now - self._last_warmup) < self._warmup_ttl_seconds:
            return True

        with self._warmup_lock:
            now = time.monotonic()
            if not force and self._last_warmup and (now - self._last_warmup) < self._warmup_ttl_seconds:
                return True
            try:
                response = self.chat(
                    [
                        {"role": "system", "content": "Reply with READY."},
                        {"role": "user", "content": "Warm up the model."},
                    ],
                    temperature=0.0,
                    max_tokens=4,
                    stream=False,
                    think=False,
                )
                self._last_warmup = time.monotonic()
                return bool(response.choices)
            except Exception:
                return False

    def stream_chat(self, messages: List[Dict],
                    temperature: float = 0.3,
                    max_tokens: int = 2048) -> Generator[str, None, None]:
        """
        Stream a chat response.

        Args:
            messages: List of message dicts
            temperature: Sampling temperature
            max_tokens: Maximum tokens

        Yields:
            Chunks of response text
        """
        for chunk in self.chat(messages, temperature, max_tokens, stream=True):
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    # ── Specialized methods ────────────────────────────────────────────────────

    def format_text(self, raw_text: str, format_type: str = "markdown") -> str:
        """
        Format raw text using the local LLM.

        Args:
            raw_text: Raw/unformatted text to clean up
            format_type: Type of formatting (markdown, json, code, summary)

        Returns:
            Formatted text
        """
        format_instructions = {
            "markdown": "Format this text as clean, well-structured Markdown. Use appropriate headings, lists, and emphasis.",
            "json": "Format this as valid, pretty-printed JSON.",
            "code": "Format this as clean, well-commented code with proper indentation.",
            "summary": "Summarize this text concisely while preserving key information."
        }

        instruction = format_instructions.get(format_type, format_instructions["markdown"])

        messages = [
            {"role": "system", "content": f"You are a text formatting assistant. {instruction}"},
            {"role": "user", "content": raw_text}
        ]

        return self.chat_complete(messages, temperature=0.1, think=False)

    def audit_prompt(self, prompt: str) -> Dict[str, Any]:
        """
        Audit a user prompt for clarity, safety, and completeness.

        Args:
            prompt: User's input prompt

        Returns:
            Audit results dict
        """
        messages = [
            {"role": "system", "content": """You are a prompt auditor. Analyze the given prompt for:
1. Clarity - Is the intent clear and unambiguous?
2. Completeness - Does it have enough context?
3. Safety - Are there any harmful or problematic requests?
4. Actionability - Can an AI assistant act on this?

Respond with a JSON object containing:
- score: 1-10 overall quality score
- issues: list of identified issues
- suggestions: list of improvement suggestions
- safe: boolean indicating if the prompt is safe
- actionable: boolean indicating if the prompt is actionable"""},
            {"role": "user", "content": prompt}
        ]

        response = self.chat_complete(
            messages,
            temperature=0.1,
            max_tokens=400,
            think=False,
            response_format={"type": "json_object"},
        )

        try:
            # Try to extract JSON from response
            start = response.find('{')
            end = response.rfind('}') + 1
            if start >= 0 and end > start:
                return json.loads(response[start:end])
        except Exception:
            pass

        return {
            "score": 5,
            "issues": ["Could not parse audit response"],
            "suggestions": ["Try rephrasing your prompt"],
            "safe": True,
            "actionable": True
        }

    def audit_response(self, response: str, prompt: str = None) -> Dict[str, Any]:
        """
        Audit an AI response for quality, accuracy, and helpfulness.

        Args:
            response: AI's response text
            prompt: Original user prompt (optional, for context)

        Returns:
            Audit results dict
        """
        context = f"Original prompt: {prompt}\n\n" if prompt else ""

        messages = [
            {"role": "system", "content": """You are a response auditor. Analyze the given AI response for:
1. Relevance - Does it address the user's query?
2. Accuracy - Is the information likely accurate?
3. Completeness - Does it fully answer the question?
4. Clarity - Is it well-organized and easy to understand?
5. Helpfulness - Does it provide value to the user?

Respond with a JSON object containing:
- score: 1-10 overall quality score
- strengths: list of positive aspects
- weaknesses: list of issues or gaps
- recommendations: suggestions for improvement
- factual_claims: list of factual claims that should be verified"""},
            {"role": "user", "content": f"{context}Response to audit:\n{response}"}
        ]

        response_text = self.chat_complete(
            messages,
            temperature=0.1,
            max_tokens=600,
            think=False,
            response_format={"type": "json_object"},
        )

        try:
            start = response_text.find('{')
            end = response_text.rfind('}') + 1
            if start >= 0 and end > start:
                return json.loads(response_text[start:end])
        except Exception:
            pass

        return {
            "score": 5,
            "strengths": [],
            "weaknesses": ["Could not parse audit response"],
            "recommendations": [],
            "factual_claims": []
        }

    def summarize(self, text: str, max_length: int = 200) -> str:
        """
        Summarize a long text.

        Args:
            text: Text to summarize
            max_length: Approximate maximum length of summary

        Returns:
            Summary text
        """
        messages = [
            {"role": "system", "content": f"Summarize the following text concisely in under {max_length} words while preserving key information."},
            {"role": "user", "content": text}
        ]

        return self.chat_complete(messages, temperature=0.1, max_tokens=1200, think=False)

    def extract_key_points(self, text: str) -> List[str]:
        """
        Extract key points from text.

        Args:
            text: Text to analyze

        Returns:
            List of key points
        """
        messages = [
            {"role": "system", "content": "Extract the key points from the following text. Return them as a bulleted list."},
            {"role": "user", "content": text}
        ]

        response = self.chat_complete(messages, temperature=0.1, think=False)

        # Parse bullet points
        points = []
        for line in response.split('\n'):
            line = line.strip()
            if line.startswith(('-', '•', '*', '–')) or line.startswith(tuple(f"{i}." for i in range(10))):
                points.append(line.lstrip('-•*–').strip())
            elif line and not line.startswith(('```', '"""')):
                points.append(line)

        return [p for p in points if p][:10]  # Limit to 10 points

    def translate(self, text: str, target_language: str = "English") -> str:
        """
        Translate text to another language.

        Args:
            text: Text to translate
            target_language: Target language

        Returns:
            Translated text
        """
        messages = [
            {"role": "system", "content": f"Translate the following text to {target_language}. Preserve formatting and meaning."},
            {"role": "user", "content": text}
        ]

        return self.chat_complete(messages, temperature=0.1, think=False)

    def generate_follow_up_questions(self, topic: str, count: int = 3) -> List[str]:
        """
        Generate follow-up questions about a topic.

        Args:
            topic: Topic to generate questions about
            count: Number of questions to generate

        Returns:
            List of follow-up questions
        """
        messages = [
            {"role": "system", "content": f"Generate {count} thoughtful follow-up questions about the following topic. Return them as a numbered list."},
            {"role": "user", "content": topic}
        ]

        response = self.chat_complete(messages, temperature=0.5, think=False)

        questions = []
        for line in response.split('\n'):
            line = line.strip()
            if line and any(line.startswith(f"{i}") for i in range(1, 10)):
                questions.append(line.split('. ', 1)[-1] if '. ' in line else line)

        return questions[:count]

    def format_for_display(self, raw_text: str, prompt: str = None) -> str:
        """
        Format raw LLM output for professional display with proper layout, style, and design.

        This method ensures:
        - Proper paragraph breaks and spacing
        - Clean markdown formatting
        - Removal of duplicate content
        - Professional text layout

        Args:
            raw_text: Raw output from the main LLM
            prompt: Original user prompt for context

        Returns:
            Professionally formatted text ready for display
        """
        context = f"Original user request: {prompt}\n\n" if prompt else ""

        messages = [
            {"role": "system", "content": """You are a professional text formatting assistant. Your job is to take raw AI-generated content and format it for beautiful, professional display.

Guidelines:
1. Ensure proper paragraph breaks - each distinct thought should be its own paragraph
2. Use appropriate Markdown formatting (headers, lists, bold, italics) where it enhances readability
3. Remove any duplicate or repeated content
4. Fix any run-on sentences or awkward line breaks
5. Ensure consistent spacing and alignment
6. Preserve all factual information and key details
7. Do NOT add new content or change the meaning - only improve formatting and layout
8. Use proper newline characters (\\n\\n) between paragraphs

Format the text to look clean and professional when rendered as Markdown."""},
            {"role": "user", "content": f"{context}Please format this text for professional display:\n\n{raw_text}"}
        ]

        return self.chat_complete(messages, temperature=0.1, think=False)


# Global instance (lazy initialization)
_local_llm: Optional[LocalLLMClient] = None


def get_local_llm(model: str = "qwen3.5:4b") -> LocalLLMClient:
    """Get or create the global local LLM client instance."""
    global _local_llm
    if _local_llm is None or _local_llm.model != model:
        _local_llm = LocalLLMClient(model=model)
    return _local_llm
