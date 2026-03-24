# test_groq.py
from groq import Groq
import os
from dotenv import load_dotenv

load_dotenv()

# 🔑 Put your Groq API key here temporarily for testing
# OR set it in .env as GROQ_API_KEY=gsk_...
GROQ_API_KEY = os.getenv("GROQ_API_KEY") or "gsk_YOUR_KEY_HERE"

print(f"🔑 API Key loaded: {GROQ_API_KEY[:20]}..." if GROQ_API_KEY else "❌ Not loaded")

if not GROQ_API_KEY or GROQ_API_KEY == "gsk_YOUR_KEY_HERE":
    print("❌ ERROR: Set your Groq API key first!")
    exit(1)

try:
    print("🔄 Testing Groq API...")

    client = Groq(api_key=GROQ_API_KEY)

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",  # Fast, free model
        messages=[
            {"role": "user", "content": "Say hello in one sentence"}
        ],
        max_tokens=50,
        temperature=0.5,
    )

    print("✅ GROQ API WORKS!")
    print(f"Response: {response.choices[0].message.content}")

except Exception as e:
    print(f"❌ GROQ API FAILED: {e}")