import os
from dotenv import load_dotenv

# Try loading from root or backend folder
load_dotenv()
load_dotenv("backend/.env")

api_key = os.getenv("GROQ_API_KEY")

# Fallback: ensure key is loaded from .env
if not api_key:
    raise ValueError("GROQ_API_KEY not found in environment")

from groq import Groq

client = Groq(api_key=api_key)

response = client.chat.completions.create(
    model="llama-3.3-70b-versatile",
    messages=[{"role": "user", "content": "Say hello and confirm you are working!"}],
)

print("\n" + "=" * 50)
print("GROQ TEST SUCCESSFUL:")
print("=" * 50)
print(response.choices[0].message.content)
print("=" * 50 + "\n")