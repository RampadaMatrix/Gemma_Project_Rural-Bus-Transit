import sys
import os
from pathlib import Path
from google import genai

# Ensure we can find secrets_config.py in ASecrets
try:
    from ASecrets.secrets_config import GOOGLE_AI_STUDIO_KEY
except ImportError:
    GOOGLE_AI_STUDIO_KEY = os.getenv("GOOGLE_AI_STUDIO_KEY", "")

class GemmaAgent:
    def __init__(self, model_id="gemma-4-31b-it"):
        if not GOOGLE_AI_STUDIO_KEY:
            raise ValueError("API Key not found. Please set GOOGLE_AI_STUDIO_KEY in secrets_config.py or environment.")
        
        self.client = genai.Client(api_key=GOOGLE_AI_STUDIO_KEY)
        self.model_id = model_id
        print(f"[SYSTEM] GemmaAgent initialized with model: {self.model_id}")

    def ask(self, prompt: str):
        """Generates a one-off response for a prompt."""
        try:
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=prompt
            )
            return response.text
        except Exception as e:
            return f"Error: {e}"

    def analyze_image(self, prompt: str, image_path: str):
        """Processes an image with a text prompt using Gemma Vision."""
        from PIL import Image
        try:
            img = Image.open(image_path)
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=[prompt, img]
            )
            return response.text
        except Exception as e:
            return f"Vision Error: {e}"

    def start_chat_session(self):
        """Starts a multi-turn chat session with history."""
        return self.client.chats.create(model=self.model_id)

def main():
    """Simple interactive CLI for Gemma."""
    try:
        agent = GemmaAgent()
        print("\n--- Gemma 4 Interactive Chat ---")
        print("Type 'exit' or 'quit' to stop.\n")
        
        # Using a chat session for memory
        chat = agent.start_chat_session()
        
        while True:
            user_input = input("You: ")
            if user_input.lower() in ["exit", "quit"]:
                break
            
            response = chat.send_message(user_input)
            print(f"\nGemma: {response.text}\n")
            
    except Exception as e:
        print(f"[FATAL] {e}")

if __name__ == "__main__":
    main()
