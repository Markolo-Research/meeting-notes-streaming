"""
AI-powered meeting summarizer using Ollama.
"""

import subprocess

from .ai_summarizer import BaseSummarizer, MeetingSummary


class OllamaSummarizer(BaseSummarizer):
    """Generates AI-powered meeting summaries using Ollama."""

    def __init__(self, model: str = "llama3.2:3b"):
        """
        Initialize summarizer.

        Args:
            model: Ollama model to use (default: llama3.2:3b)
        """
        self.model = model

    def summarize(self, transcript: str, user_notes: str = "") -> MeetingSummary:
        """
        Generate an AI summary of a meeting transcript.

        Args:
            transcript: Full meeting transcript text
            user_notes: Optional notes written by user during recording

        Returns:
            MeetingSummary with structured data
        """
        print(f"Generating AI summary with {self.model}...")

        prompt = self._build_prompt(transcript, user_notes=user_notes)
        response = self._call_ollama(prompt)
        summary = self._parse_response(response)

        return summary

    def _call_ollama(self, prompt: str) -> str:
        """Call Ollama API and get response."""
        try:
            # Use ollama run command
            result = subprocess.run(
                ["ollama", "run", self.model, prompt],
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
            )

            if result.returncode != 0:
                raise RuntimeError(f"Ollama failed: {result.stderr}")

            return result.stdout.strip()

        except subprocess.TimeoutExpired:
            raise RuntimeError("Ollama summarization timed out (5 minutes)")
        except FileNotFoundError:
            raise RuntimeError("Ollama not found. Is it installed?")


# TODO: Clean this up?
if __name__ == "__main__":
    # Test with a sample transcript
    sample = """
    [00:00] Hey everyone, thanks for joining. Let's discuss the Q1 roadmap.
    [00:15] Sure, I think we should prioritize the user dashboard first.
    [00:30] I agree. And we need to fix the auth bug by end of week.
    [00:45] Okay, I'll take that action item. Sarah, can you handle the dashboard design?
    [01:00] Yes, I'll have mockups ready by Thursday.
    """

    summarizer = OllamaSummarizer()
    result = summarizer.summarize(sample)

    print("\n=== TEST SUMMARY ===")
    print(f"Overview: {result.overview}")
    print(f"Key Points: {result.key_points}")
    print(f"Action Items: {result.action_items}")
    print(f"Decisions: {result.decisions}")
    print(f"Participants: {result.participants}")
