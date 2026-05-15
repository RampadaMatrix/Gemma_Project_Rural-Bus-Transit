from google import genai
from secrets_config import GOOGLE_AI_STUDIO_KEY

# Using the new official google-genai SDK
client = genai.Client(api_key=GOOGLE_AI_STUDIO_KEY)

print("Available models:")
try:
    # New syntax for listing models
    for model in client.models.list():
        print(model.name)
except Exception as e:
    print(f"Error listing models: {e}")
