print("🔥🔥🔥 RUNNING THIS APP.PY FILE 🔥🔥🔥")

import os
import json
import re
import uuid
import traceback
import time
import logging
from datetime import datetime, timedelta, timezone
from io import BytesIO
from functools import wraps

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from dotenv import load_dotenv
from pymongo import MongoClient
import bcrypt
import jwt
from PyPDF2 import PdfReader
from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import green, red
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from werkzeug.utils import secure_filename
from groq import Groq
import pytesseract
from PIL import Image
import cv2
import numpy as np

# ===============================
# LOGGING SETUP
# ===============================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ===============================
# LOAD ENV
# ===============================
load_dotenv()

# ===============================
# ENVIRONMENT VARIABLES CHECK
# ===============================
print("=" * 50)
print("🔑 Environment Variables Check:")

JWT_SECRET = os.getenv("JWT_SECRET")
MONGO_URI = os.getenv("MONGO_URI")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

print(f"   JWT_SECRET: {'✅ SET' if JWT_SECRET else '❌ NOT SET'}")
print(f"   MONGO_URI: {'✅ SET' if MONGO_URI else '❌ NOT SET'}")
print(f"   GROQ_API_KEY: {GROQ_API_KEY[:20]}..." if GROQ_API_KEY else "   ❌ NOT SET")
print("=" * 50)

if not JWT_SECRET:
    raise RuntimeError("❌ JWT_SECRET environment variable is required")
if not MONGO_URI:
    raise RuntimeError("❌ MONGO_URI environment variable is required")
if not GROQ_API_KEY:
    raise RuntimeError("❌ GROQ_API_KEY environment variable is required")

# ===============================
# GROQ CLIENT SETUP
# ===============================
groq_client = Groq(api_key=GROQ_API_KEY)
print("✅ Groq client initialized")

# ===============================
# PYTESSERACT SETUP - DOCKER FRIENDLY
# ===============================
import platform

print("🔄 Checking Pytesseract availability...")

# Set Tesseract path for different environments
if os.getenv('RENDER'):  # Running on Render
    pytesseract.pytesseract.tesseract_cmd = '/usr/bin/tesseract'
    print("🐳 Running in Docker/Render environment")
elif platform.system() == 'Windows':
    possible_paths = [
        r'C:\Program Files\Tesseract-OCR\tesseract.exe',
        r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
    ]
    for path in possible_paths:
        if os.path.exists(path):
            pytesseract.pytesseract.tesseract_cmd = path
            print(f"✅ Found Tesseract at: {path}")
            break
elif platform.system() == 'Darwin':  # macOS
    if os.path.exists('/opt/homebrew/bin/tesseract'):
        pytesseract.pytesseract.tesseract_cmd = '/opt/homebrew/bin/tesseract'
    elif os.path.exists('/usr/local/bin/tesseract'):
        pytesseract.pytesseract.tesseract_cmd = '/usr/local/bin/tesseract'
else:  # Linux
    pytesseract.pytesseract.tesseract_cmd = '/usr/bin/tesseract'

try:
    version = pytesseract.get_tesseract_version()
    print(f"✅ Pytesseract available - Version: {version}")
    ocr_available = True
except Exception as e:
    print(f"⚠️ Pytesseract not available: {e}")
    print("⚠️ Image uploads will be unavailable")
    ocr_available = False
# ===============================
# FLASK APP
# ===============================
app = Flask(__name__)

# 🔥 CORS CONFIGURATION
ALLOWED_ORIGINS = [
    "http://localhost:*",
    "http://127.0.0.1:*",
    "http://10.0.2.2:*",
    "https://your-app-name.onrender.com",
    "*"
]

CORS(app, resources={
    r"/*": {
        "origins": "*",
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "supports_credentials": True
    }
})

app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10MB

# ===============================
# UPLOAD CONFIG
# ===============================
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

# ===============================
# MONGODB
# ===============================
try:
    mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    mongo_client.admin.command("ping")
    db = mongo_client.get_default_database()
    users_collection = db["users"]
    history_collection = db["history"]
    learning_progress_collection = db["learning_progress"]
    users_collection.create_index("email", unique=True)
    print("✅ MongoDB connected")
except Exception as e:
    print(f"❌ MongoDB connection error: {e}")
    logger.error(f"MongoDB connection error: {e}")
    raise SystemExit(1)

# ===============================
# RESUME KEYWORDS
# ===============================
RESUME_KEYWORDS = [
    "experience", "education", "skills", "work", "project",
    "university", "college", "degree", "bachelor", "master",
    "email", "phone", "linkedin", "github", "portfolio",
    "developed", "managed", "led", "created", "implemented",
    "engineer", "developer", "manager", "analyst", "designer",
    "resume", "cv", "objective", "summary", "certifications",
]

def text_looks_like_resume(text, strict=False):
    """Check if text looks like a resume"""
    if not text or len(text.strip()) < 100:
        return False
    text_lower = text.lower()
    matches = [kw for kw in RESUME_KEYWORDS if kw in text_lower]
    count = len(matches)
    min_required = 5 if strict else 3
    return count >= min_required

def validate_job_description(job_desc):
    """Validate job description format"""
    if not job_desc or len(job_desc.strip()) < 20:
        return False, ""
    cleaned = job_desc.strip()
    job_keywords = ["experience", "required", "responsibilities", "skills", "role", "position"]
    keyword_matches = sum(1 for kw in job_keywords if kw in cleaned.lower())
    return keyword_matches >= 2, cleaned

# ===============================
# AUTH MIDDLEWARE
# ===============================
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            return jsonify({"error": "Authentication required"}), 401
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            request.user_id = payload["userId"]
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token"}), 401
        return f(*args, **kwargs)
    return decorated

# ===============================
# HELPER FUNCTIONS
# ===============================

def safe_parse(val):
    """Safely parse JSON or list"""
    if not val:
        return []
    if isinstance(val, list):
        return val
    try:
        return json.loads(val)
    except:
        return []

def allowed_file(filename):
    """Check if file extension is allowed"""
    return os.path.splitext(filename)[1].lower() in ALLOWED_EXTENSIONS

def get_file_extension(filename):
    """Get file extension"""
    return os.path.splitext(filename)[1].lower()

def is_image_file(filename):
    """Check if file is an image"""
    return get_file_extension(filename) in {".png", ".jpg", ".jpeg", ".gif", ".webp"}

def extract_text_from_pdf(file_path):
    """Extract text from PDF file"""
    try:
        logger.info(f"Extracting text from PDF: {file_path}")
        reader = PdfReader(file_path)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        logger.info(f"PDF extraction successful: {len(text)} chars")
        return text
    except Exception as e:
        logger.error(f"PDF extraction error: {e}")
        raise Exception(f"Failed to extract PDF: {str(e)}")

def preprocess_image(image_path):
    """Preprocess image for better OCR results"""
    try:
        # Read image
        img = cv2.imread(image_path)
        if img is None:
            raise Exception("Failed to read image")

        # Convert to grayscale
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # Apply thresholding
        _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)

        # Denoise
        denoised = cv2.medianBlur(thresh, 3)

        # Upscale for better OCR
        scale_percent = 150
        width = int(denoised.shape[1] * scale_percent / 100)
        height = int(denoised.shape[0] * scale_percent / 100)
        dim = (width, height)
        resized = cv2.resize(denoised, dim, interpolation=cv2.INTER_CUBIC)

        return resized
    except Exception as e:
        logger.warning(f"Image preprocessing failed: {e}, using original")
        return cv2.imread(image_path)

def extract_text_from_image(file_path):
    """Extract text from image using Pytesseract - LIGHTWEIGHT"""
    if not ocr_available:
        raise Exception("OCR not available. Please upload a PDF instead.")

    try:
        logger.info(f"Extracting text from image using Pytesseract: {file_path}")
        print("🔍 Using Pytesseract for OCR...")

        # Preprocess image for better results
        processed_image = preprocess_image(file_path)

        # Extract text using Pytesseract
        text = pytesseract.image_to_string(processed_image)

        # Clean up text
        text = text.strip()
        text = '\n'.join(line.strip() for line in text.split('\n') if line.strip())

        logger.info(f"Image extraction successful: {len(text)} chars")
        print(f"✅ Extracted {len(text)} chars")
        return text
    except Exception as e:
        logger.error(f"OCR error: {e}")
        raise Exception(f"Failed to extract image: {str(e)}")

def text_quality_check(text, min_chars=50):
    """Check text quality - more lenient"""
    if not text or len(text.strip()) < min_chars:
        return False, f"Text too short ({len(text.strip())} chars, need {min_chars})"

    # Check for actual content (not just spaces/newlines)
    meaningful_text = ''.join(c for c in text if c.isalnum() or c.isspace())
    if len(meaningful_text) < min_chars:
        return False, "No meaningful content found"

    return True, ""

def save_uploaded_file(file):
    """Save uploaded file to disk"""
    try:
        ext = get_file_extension(file.filename)
        unique_name = f"{uuid.uuid4().hex}{ext}"
        file_path = os.path.join(UPLOAD_DIR, unique_name)
        file.save(file_path)
        logger.info(f"File saved: {file_path}")
        return file_path
    except Exception as e:
        logger.error(f"File save error: {e}")
        raise

def cleanup_file(file_path):
    """Delete uploaded file after processing"""
    try:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"File cleaned up: {file_path}")
    except Exception as e:
        logger.warning(f"Cleanup error: {e}")

def extract_json_from_response(raw_text):
    """Extract JSON from AI response"""
    if not raw_text:
        raise ValueError("Empty response from AI")

    # Remove markdown code blocks
    cleaned = re.sub(r'```json\s*', '', raw_text)
    cleaned = re.sub(r'```\s*$', '', cleaned)
    cleaned = cleaned.strip()

    # Try to find JSON object
    match = re.search(r'\{[\s\S]*\}', cleaned)
    if not match:
        logger.error(f"No JSON found in response: {raw_text[:200]}")
        raise ValueError("No JSON object found in response")

    json_str = match.group(0)

    # Try to parse
    try:
        parsed = json.loads(json_str)
        return parsed
    except json.JSONDecodeError as e:
        logger.warning(f"JSON decode error, attempting fix: {e}")

        # Try to fix common issues
        json_str = json_str.replace('\\"', '"')
        json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)  # Remove trailing commas

        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            logger.error(f"JSON parsing failed: {str(e)}\nJSON: {json_str[:500]}")
            raise ValueError(f"Invalid JSON in response: {str(e)}")

def validate_file_upload(request_obj):
    """Validate uploaded file"""
    if "resume" not in request_obj.files:
        return None, (jsonify({"error": "No file uploaded"}), 400)

    file = request_obj.files["resume"]

    if not file.filename:
        return None, (jsonify({"error": "No filename"}), 400)

    # Check file extension
    if not allowed_file(file.filename):
        return None, (jsonify({
            "error": f"Invalid file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
        }), 400)

    # Check file size
    file.seek(0, 2)  # Seek to end
    file_size = file.tell()
    file.seek(0)  # Reset

    if file_size == 0:
        return None, (jsonify({"error": "File is empty"}), 400)

    if file_size > MAX_FILE_SIZE:
        size_mb = file_size / 1024 / 1024
        return None, (jsonify({
            "error": f"File too large ({size_mb:.1f}MB). Max 5MB."
        }), 400)

    logger.info(f"File validated: {file.filename} ({file_size} bytes)")
    return file, None

# ===============================
# SKILL LEARNING RESOURCES
# ===============================

def get_quick_learning_tips(skills):
    """Get quick learning tips for missing skills"""
    try:
        tips_map = {
            # Programming Languages
            "python": {"platform": "freeCodeCamp, Codecademy", "time": "2-3 weeks", "type": "Programming Language"},
            "javascript": {"platform": "JavaScript.info, freeCodeCamp", "time": "2-4 weeks", "type": "Programming Language"},
            "java": {"platform": "Java Programming MOOC, Codecademy", "time": "4-6 weeks", "type": "Programming Language"},
            "c++": {"platform": "LearnCpp.com, Udemy", "time": "4-6 weeks", "type": "Programming Language"},
            "c#": {"platform": "Microsoft Learn, Udemy", "time": "3-4 weeks", "type": "Programming Language"},
            "typescript": {"platform": "TypeScript Official Docs, Udemy", "time": "1-2 weeks", "type": "Programming Language"},
            "go": {"platform": "Tour of Go, Udemy", "time": "2-3 weeks", "type": "Programming Language"},
            "rust": {"platform": "The Rust Book, Rustlings", "time": "4-6 weeks", "type": "Programming Language"},
            "php": {"platform": "PHP.net tutorial, Laracasts", "time": "2-3 weeks", "type": "Programming Language"},
            "ruby": {"platform": "Ruby Koans, Codecademy", "time": "3-4 weeks", "type": "Programming Language"},
            "kotlin": {"platform": "Kotlin Official Docs, Udemy", "time": "2-3 weeks", "type": "Programming Language"},

            # Frontend
            "react": {"platform": "React.dev official docs, Scrimba", "time": "2-3 weeks", "type": "Frontend Framework"},
            "vue": {"platform": "Vue Mastery, Vue.js official guide", "time": "2-3 weeks", "type": "Frontend Framework"},
            "angular": {"platform": "Angular.io official tutorial", "time": "3-4 weeks", "type": "Frontend Framework"},
            "svelte": {"platform": "Svelte tutorial, YouTube", "time": "1-2 weeks", "type": "Frontend Framework"},
            "next.js": {"platform": "Next.js official docs, Vercel", "time": "2 weeks", "type": "Frontend Framework"},
            "html": {"platform": "freeCodeCamp, MDN Web Docs", "time": "1 week", "type": "Frontend"},
            "css": {"platform": "CSS-Tricks, freeCodeCamp", "time": "2 weeks", "type": "Frontend"},
            "sass": {"platform": "Sass official guide, Udemy", "time": "1 week", "type": "CSS Framework"},
            "tailwind": {"platform": "Tailwind official docs, YouTube", "time": "1 week", "type": "CSS Framework"},
            "bootstrap": {"platform": "Bootstrap official docs, freeCodeCamp", "time": "1 week", "type": "CSS Framework"},

            # Backend
            "node.js": {"platform": "NodeSchool, freeCodeCamp", "time": "2-3 weeks", "type": "Backend"},
            "express": {"platform": "Express.js official guide, Udemy", "time": "1-2 weeks", "type": "Backend Framework"},
            "django": {"platform": "Django official tutorial, Corey Schafer", "time": "3-4 weeks", "type": "Backend Framework"},
            "flask": {"platform": "Flask Mega-Tutorial, freeCodeCamp", "time": "2 weeks", "type": "Backend Framework"},
            "spring boot": {"platform": "Spring.io guides, Udemy", "time": "3-4 weeks", "type": "Backend Framework"},
            "fastapi": {"platform": "FastAPI official docs, YouTube", "time": "1-2 weeks", "type": "Backend Framework"},
            "laravel": {"platform": "Laracasts, Laravel Bootcamp", "time": "3-4 weeks", "type": "Backend Framework"},
            "ruby on rails": {"platform": "Rails tutorial, GoRails", "time": "4-6 weeks", "type": "Backend Framework"},

            # Database
            "sql": {"platform": "SQLBolt, Mode Analytics SQL Tutorial", "time": "2 weeks", "type": "Database"},
            "mysql": {"platform": "MySQL official tutorial, Udemy", "time": "2 weeks", "type": "Database"},
            "postgresql": {"platform": "PostgreSQL Tutorial, Udemy", "time": "2 weeks", "type": "Database"},
            "mongodb": {"platform": "MongoDB University (free), freeCodeCamp", "time": "2 weeks", "type": "Database"},
            "redis": {"platform": "Redis University (free)", "time": "1 week", "type": "Database"},
            "dynamodb": {"platform": "AWS DynamoDB Guide, Udemy", "time": "1-2 weeks", "type": "Database"},
            "cassandra": {"platform": "DataStax Academy (free)", "time": "2-3 weeks", "type": "Database"},

            # DevOps & Cloud
            "docker": {"platform": "Docker official tutorial, TechWorld with Nana", "time": "1-2 weeks", "type": "DevOps"},
            "kubernetes": {"platform": "Kubernetes official tutorial, KodeKloud", "time": "3-4 weeks", "type": "DevOps"},
            "aws": {"platform": "AWS Free Tier, A Cloud Guru", "time": "4-6 weeks", "type": "Cloud"},
            "azure": {"platform": "Microsoft Learn (free), Udemy", "time": "4-6 weeks", "type": "Cloud"},
            "gcp": {"platform": "Google Cloud Skills Boost", "time": "4-6 weeks", "type": "Cloud"},
            "terraform": {"platform": "HashiCorp Learn, Udemy", "time": "2-3 weeks", "type": "DevOps"},
            "ansible": {"platform": "Ansible official docs, YouTube", "time": "2 weeks", "type": "DevOps"},
            "jenkins": {"platform": "Jenkins official tutorial, Udemy", "time": "2 weeks", "type": "CI/CD"},
            "git": {"platform": "Git official tutorial, Oh My Git game", "time": "1 week", "type": "Version Control"},
            "github": {"platform": "GitHub Skills, freeCodeCamp", "time": "1 week", "type": "Version Control"},
            "gitlab": {"platform": "GitLab Learn, YouTube", "time": "1 week", "type": "Version Control"},
            "ci/cd": {"platform": "GitLab CI/CD tutorial, Jenkins tutorial", "time": "2 weeks", "type": "DevOps"},

            # Data Science & ML
            "machine learning": {"platform": "Coursera Andrew Ng, fast.ai", "time": "8-12 weeks", "type": "AI/ML"},
            "deep learning": {"platform": "fast.ai, Coursera", "time": "8-12 weeks", "type": "AI/ML"},
            "data analysis": {"platform": "Kaggle Learn, DataCamp", "time": "4-6 weeks", "type": "Data Science"},
            "data science": {"platform": "Kaggle, DataCamp", "time": "8-12 weeks", "type": "Data Science"},
            "pandas": {"platform": "Kaggle, Real Python", "time": "2 weeks", "type": "Data Science"},
            "numpy": {"platform": "NumPy official tutorial, Real Python", "time": "1 week", "type": "Data Science"},
            "scikit-learn": {"platform": "Scikit-learn docs, Kaggle", "time": "2-3 weeks", "type": "AI/ML"},
            "tensorflow": {"platform": "TensorFlow official tutorial", "time": "4-6 weeks", "type": "AI/ML"},
            "pytorch": {"platform": "PyTorch official tutorial, fast.ai", "time": "4-6 weeks", "type": "AI/ML"},
            "keras": {"platform": "Keras official guide", "time": "2-3 weeks", "type": "AI/ML"},
            "nlp": {"platform": "Hugging Face, Coursera", "time": "4-6 weeks", "type": "AI/ML"},
            "computer vision": {"platform": "OpenCV, PyImageSearch", "time": "4-6 weeks", "type": "AI/ML"},

            # Testing
            "unit testing": {"platform": "Test Automation University (free)", "time": "2 weeks", "type": "Testing"},
            "jest": {"platform": "Jest official docs, Udemy", "time": "1 week", "type": "Testing"},
            "pytest": {"platform": "Real Python, Pytest docs", "time": "1 week", "type": "Testing"},
            "selenium": {"platform": "Test Automation University, Udemy", "time": "2-3 weeks", "type": "Testing"},
            "cypress": {"platform": "Cypress official docs, YouTube", "time": "1-2 weeks", "type": "Testing"},

            # Mobile
            "react native": {"platform": "React Native docs, Udemy", "time": "3-4 weeks", "type": "Mobile"},
            "flutter": {"platform": "Flutter official docs, Udemy", "time": "3-4 weeks", "type": "Mobile"},
            "swift": {"platform": "Swift Playgrounds, Hacking with Swift", "time": "4-6 weeks", "type": "Mobile"},
            "android": {"platform": "Android official codelabs, Udemy", "time": "4-6 weeks", "type": "Mobile"},
            "ios": {"platform": "Apple Developer, Hacking with Swift", "time": "4-6 weeks", "type": "Mobile"},

            # Other Skills
            "agile": {"platform": "Scrum.org (free), Coursera", "time": "1-2 weeks", "type": "Methodology"},
            "scrum": {"platform": "Scrum.org guides (free)", "time": "1 week", "type": "Methodology"},
            "rest api": {"platform": "freeCodeCamp, Postman Learning", "time": "1-2 weeks", "type": "API"},
            "graphql": {"platform": "How to GraphQL, Udemy", "time": "2 weeks", "type": "API"},
            "microservices": {"platform": "Udemy, YouTube", "time": "3-4 weeks", "type": "Architecture"},
            "system design": {"platform": "System Design Primer, YouTube", "time": "4-8 weeks", "type": "Architecture"},
            "linux": {"platform": "Linux Journey, Udemy", "time": "3-4 weeks", "type": "OS"},
            "bash": {"platform": "Bash Scripting Tutorial, Linux Academy", "time": "1-2 weeks", "type": "Scripting"},
            "powershell": {"platform": "Microsoft Learn, Udemy", "time": "2 weeks", "type": "Scripting"},
        }

        tips = []
        for skill in skills[:5]:  # Top 5 skills
            skill_lower = skill.lower().strip()
            matched = False

            # Try exact match first
            for key in tips_map:
                if key in skill_lower or skill_lower in key:
                    info = tips_map[key]
                    tips.append({
                        "skill": skill,
                        "type": info["type"],
                        "quickStart": f"Start with: {info['platform']}",
                        "estimatedTime": info["time"],
                        "difficulty": "Beginner to Intermediate",
                        "isFree": True
                    })
                    matched = True
                    break

            if not matched:
                # Generic tip for unknown skills
                tips.append({
                    "skill": skill,
                    "type": "General",
                    "quickStart": "Search on: Udemy, Coursera, YouTube, freeCodeCamp",
                    "estimatedTime": "2-4 weeks for basics",
                    "difficulty": "Varies",
                    "isFree": "Mixed (Free & Paid options available)"
                })

        logger.info(f"Generated quick tips for {len(tips)} skills")
        return tips
    except Exception as e:
        logger.error(f"Error in get_quick_learning_tips: {e}")
        traceback.print_exc()
        return []


def get_detailed_skill_resources(skills):
    """Get detailed learning resources using AI for top missing skills"""
    try:
        if not skills or len(skills) == 0:
            return []

        # Limit to top 3 to avoid token limits
        top_skills = skills[:3]
        logger.info(f"Generating detailed resources for: {top_skills}")

        prompt = f"""Generate learning resources for these skills: {', '.join(top_skills)}

For EACH skill, provide learning path with REAL resources.

Return STRICT JSON (no markdown, just JSON):
{{
  "resources": [
    {{
      "skill": "skill name",
      "priority": "High",
      "estimatedTime": "2 weeks",
      "freeOptions": [
        {{
          "name": "resource name",
          "type": "Video",
          "platform": "YouTube",
          "url": "youtube.com/...",
          "description": "brief description"
        }}
      ],
      "paidOptions": [
        {{
          "name": "resource name",
          "platform": "Udemy",
          "price": "$20",
          "rating": "4.5/5"
        }}
      ],
      "practiceProjects": ["project1", "project2"],
      "certifications": ["cert1"],
      "topTips": ["tip1", "tip2", "tip3"]
    }}
  ]
}}"""

        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            temperature=0.3,
            messages=[
                {
                    "role": "system",
                    "content": "You are a career learning advisor. Return ONLY valid JSON without markdown. Recommend real resources."
                },
                {"role": "user", "content": prompt}
            ],
            max_tokens=2500,
            timeout=30
        )

        raw_response = completion.choices[0].message.content
        logger.info(f"AI Resources Response received ({len(raw_response)} chars)")
        print(f"🤖 AI Resources Response (first 300 chars): {raw_response[:300]}...")

        parsed = extract_json_from_response(raw_response)
        resources = parsed.get("resources", [])
        logger.info(f"Generated detailed resources for {len(resources)} skills")

        return resources

    except Exception as e:
        logger.error(f"Error in get_detailed_skill_resources: {e}")
        traceback.print_exc()
        return []

# ===============================
# ROUTES
# ===============================

@app.route("/", methods=["GET"])
def root():
    """Root endpoint"""
    return jsonify({
        "status": "Resume Analyzer Backend Running ✅",
        "version": "2.0",
        "features": ["analyze", "optimize", "interview", "cover-letter", "learning-resources"],
        "ocr": "pytesseract" if ocr_available else "none"
    })

@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    try:
        mongo_client.admin.command("ping")
        db_status = "connected"
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        db_status = "disconnected"

    return jsonify({
        "status": "running",
        "database": db_status,
        "ai": "groq",
        "ocr": "pytesseract" if ocr_available else "unavailable",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }), 200 if db_status == "connected" else 503

@app.route("/register", methods=["POST"])
def register():
    """Register new user"""
    try:
        data = request.get_json()
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")

        if not email or len(password) < 6:
            return jsonify({"error": "Invalid email or password (min 6 chars)"}), 400

        if users_collection.find_one({"email": email}):
            return jsonify({"error": "User already exists"}), 400

        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
        users_collection.insert_one({
            "email": email,
            "password": hashed.decode(),
            "createdAt": datetime.now(timezone.utc)
        })

        logger.info(f"New user registered: {email}")
        return jsonify({"success": True, "message": "Registration successful"})
    except Exception as e:
        logger.error(f"Registration error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/login", methods=["POST"])
def login():
    """Login user"""
    try:
        data = request.get_json()
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")

        user = users_collection.find_one({"email": email})
        if not user or not bcrypt.checkpw(password.encode(), user["password"].encode()):
            return jsonify({"error": "Invalid credentials"}), 401

        token = jwt.encode({
            "userId": str(user["_id"]),
            "exp": datetime.now(timezone.utc) + timedelta(days=7)
        }, JWT_SECRET, algorithm="HS256")

        logger.info(f"User logged in: {email}")
        return jsonify({"success": True, "token": token})
    except Exception as e:
        logger.error(f"Login error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/analyze", methods=["POST"])
@require_auth
def analyze():
    """Analyze resume"""
    file_path = None
    try:
        print("\n" + "="*70)
        print("📥 ANALYZE REQUEST")
        print("="*70)
        logger.info(f"Analyze request from user: {request.user_id}")
        print(f"User ID: {request.user_id}")

        # ✅ Validate file
        file, error = validate_file_upload(request)
        if error:
            return error

        job_description = request.form.get("jobDescription", "").strip()
        logger.info(f"Job description provided: {bool(job_description)}")

        # ✅ Save file
        try:
            file_path = save_uploaded_file(file)
        except Exception as e:
            logger.error(f"File save failed: {e}")
            return jsonify({"error": f"Failed to save file: {str(e)}"}), 400

        ext = get_file_extension(file.filename)
        is_image = is_image_file(file.filename)

        print(f"📄 File: {file.filename}")
        print(f"📄 Extension: {ext}")
        print(f"📄 Is image: {is_image}")
        logger.info(f"File info - Extension: {ext}, Is image: {is_image}")

        # ✅ Extract text with better error handling
        text = ""
        try:
            if ext == ".pdf":
                logger.info("Processing PDF...")
                print("📑 Processing PDF...")
                text = extract_text_from_pdf(file_path)
                print(f"📑 Extracted {len(text)} chars from PDF")
                logger.info(f"PDF extraction successful: {len(text)} chars")

            elif is_image:
                logger.info("Processing image with Pytesseract...")
                print("🖼️ Processing Image with Pytesseract...")
                if not ocr_available:
                    logger.warning("OCR not available, rejecting image upload")
                    return jsonify({
                        "error": "Image processing not available. Please upload a PDF.",
                        "suggestion": "Convert your resume to PDF format",
                        "ocrAvailable": False
                    }), 400
                text = extract_text_from_image(file_path)
                print(f"🖼️ Extracted {len(text)} chars from image")
                logger.info(f"Image extraction successful: {len(text)} chars")
            else:
                logger.error(f"Invalid file type: {ext}")
                return jsonify({"error": "Unsupported file type"}), 400

        except Exception as e:
            logger.error(f"Text extraction error: {e}")
            print(f"❌ Text extraction error: {e}")
            traceback.print_exc()
            return jsonify({
                "error": f"Failed to extract text: {str(e)}",
                "suggestion": "Try uploading a clearer image or try a PDF",
                "details": str(e)
            }), 400

        # ✅ Validate text quality (more lenient)
        is_valid, validation_msg = text_quality_check(text, min_chars=50)
        if not is_valid:
            logger.warning(f"Text validation failed: {validation_msg}")
            print(f"❌ Text validation failed: {validation_msg}")
            print(f"Text length: {len(text)} chars")
            return jsonify({
                "error": f"Could not extract resume content: {validation_msg}",
                "textLength": len(text),
                "suggestion": "Upload a clearer image or try a PDF"
            }), 400

        logger.info(f"✅ Text validated: {len(text)} chars")
        print(f"✅ Text validated: {len(text)} chars")
        print("✅ Proceeding to analysis...")

        # ✅ Analyze with error handling
        return analyze_with_text(text, job_description)

    except Exception as e:
        logger.error(f"Unexpected error in analyze: {e}")
        print(f"❌ Unexpected error: {e}")
        print(traceback.format_exc())
        return jsonify({
            "error": "An unexpected error occurred",
            "details": str(e)
        }), 500
    finally:
        cleanup_file(file_path)


def analyze_with_text(text, job_description):
    """Analyze resume text with retry logic"""
    has_valid_job, cleaned_job = validate_job_description(job_description)
    logger.info(f"Valid job description: {has_valid_job}")

    # Build prompt
    if not has_valid_job:
        prompt = f"""Analyze this resume (no job description provided).

Resume text:
{text[:4000]}

Provide analysis in JSON format (NO markdown, just raw JSON):
{{
  "atsScore": 75,
  "fitScore": 0,
  "skillStrength": {{}},
  "matchingSkills": [],
  "missingSkills": [],
  "aiDetection": {{"aiProbability": 0, "riskLevel": "Low", "flaggedSections": [], "reasons": [], "suggestions": []}},
  "overallFeedback": "2-3 sentences about the resume quality"
}}"""
    else:
        prompt = f"""Analyze resume vs job description.

Resume:
{text[:4000]}

Job Description:
{cleaned_job}

Provide analysis in JSON format (NO markdown, just raw JSON):
{{
  "atsScore": 75,
  "fitScore": 68,
  "skillStrength": {{"Python": 8, "SQL": 7}},
  "matchingSkills": ["Python", "SQL"],
  "missingSkills": ["Docker", "AWS"],
  "aiDetection": {{"aiProbability": 15, "riskLevel": "Low", "flaggedSections": [], "reasons": [], "suggestions": []}},
  "overallFeedback": "2-3 sentences"
}}"""

    # ✅ Retry logic for API calls
    max_retries = 3
    parsed = None

    for attempt in range(max_retries):
        try:
            logger.info(f"Calling Groq API (attempt {attempt + 1}/{max_retries})...")
            print(f"🤖 Calling Groq API (attempt {attempt + 1}/{max_retries})...")

            completion = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                temperature=0.2,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a professional resume analyzer. Return ONLY valid JSON without any markdown formatting or code blocks. Start with { and end with }."
                    },
                    {"role": "user", "content": prompt}
                ],
                max_tokens=2000,
                timeout=30
            )

            raw_response = completion.choices[0].message.content
            logger.info(f"Groq response received ({len(raw_response)} chars)")
            print(f"✅ Groq response received ({len(raw_response)} chars)")

            # ✅ Try to parse JSON
            try:
                parsed = extract_json_from_response(raw_response)
                logger.info("JSON parsed successfully")
                print("✅ JSON parsed successfully")
                break
            except ValueError as je:
                logger.warning(f"JSON parsing failed (attempt {attempt + 1}): {je}")
                print(f"⚠️ JSON parsing failed (attempt {attempt + 1}): {je}")
                if attempt == max_retries - 1:
                    logger.error("Max retries reached for JSON parsing")
                    raise
                time.sleep(1)  # Wait before retry
                continue

        except Exception as e:
            logger.error(f"Groq API error (attempt {attempt + 1}): {e}")
            print(f"❌ Groq API error (attempt {attempt + 1}): {e}")
            if attempt == max_retries - 1:
                logger.error("Max retries reached")
                return jsonify({
                    "error": "Failed to analyze resume",
                    "details": str(e)
                }), 500
            time.sleep(2)  # Wait before retry
            continue

    if not parsed:
        logger.error("Failed to parse response after all retries")
        return jsonify({
            "error": "Failed to parse analysis response"
        }), 500

    # ✅ Add default values if job description missing
    if not has_valid_job:
        parsed["fitScore"] = 0
        parsed["matchingSkills"] = []
        parsed["missingSkills"] = []

    # ✅ Add learning resources (with fallback)
    missing_skills = parsed.get("missingSkills", [])
    parsed["skillResourcesAvailable"] = False

    if missing_skills and len(missing_skills) > 0:
        logger.info(f"Generating resources for {len(missing_skills)} skills...")
        print(f"🎓 Generating resources for {len(missing_skills)} skills...")

        try:
            quick_tips = get_quick_learning_tips(missing_skills[:5])
            if quick_tips:
                parsed["quickLearningTips"] = quick_tips
                parsed["skillResourcesAvailable"] = True
                logger.info(f"Added {len(quick_tips)} quick tips")
                print(f"✅ Added {len(quick_tips)} quick tips")
        except Exception as e:
            logger.warning(f"Quick tips generation failed: {e}")
            print(f"⚠️ Quick tips failed: {e}")

        # Optional: detailed resources (less critical)
        try:
            detailed = get_detailed_skill_resources(missing_skills[:3])
            if detailed:
                parsed["learningResources"] = detailed
                logger.info(f"Added detailed resources for {len(detailed)} skills")
                print(f"✅ Added detailed resources")
        except Exception as e:
            logger.warning(f"Detailed resources generation failed (non-critical): {e}")
            print(f"⚠️ Detailed resources failed (non-critical): {e}")
    else:
        logger.info("No missing skills found")
        print("ℹ️ No missing skills found, skipping resource generation")

    logger.info("Analysis complete")
    print("✅ Analysis complete")
    return jsonify({"success": True, **parsed})


@app.route("/optimize-resume", methods=["POST"])
@require_auth
def optimize_resume():
    """Optimize resume for job description"""
    file_path = None
    try:
        logger.info(f"Optimize resume request from user: {request.user_id}")

        file, error = validate_file_upload(request)
        if error:
            return error

        job_desc = request.form.get("jobDescription", "").strip()
        has_valid, cleaned = validate_job_description(job_desc)

        if not has_valid:
            logger.warning("Invalid job description provided")
            return jsonify({"error": "Provide valid job description", "needsJobDescription": True}), 400

        file_path = save_uploaded_file(file)
        ext = get_file_extension(file.filename)

        text = extract_text_from_pdf(file_path) if ext == ".pdf" else extract_text_from_image(file_path)

        if not text.strip() or not text_looks_like_resume(text, strict=True):
            logger.warning("File does not look like a resume")
            return jsonify({"error": "Invalid resume", "notResume": True}), 400

        prompt = f"""Optimize resume for job.

Resume: {text[:3500]}
Job: {cleaned}

Return JSON (no markdown):
{{
  "optimizedSummary": "improved summary",
  "rewrittenExperience": ["bullet1", "bullet2"],
  "addedKeywords": ["kw1", "kw2"]
}}"""

        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            temperature=0.3,
            messages=[
                {"role": "system", "content": "Return only JSON without markdown"},
                {"role": "user", "content": prompt}
            ],
            timeout=30
        )

        parsed = extract_json_from_response(completion.choices[0].message.content)
        logger.info("Resume optimization successful")
        return jsonify({"success": True, **parsed})
    except Exception as e:
        logger.error(f"Optimize resume error: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        cleanup_file(file_path)

@app.route("/interview", methods=["POST"])
@require_auth
def interview():
    """Generate interview preparation"""
    file_path = None
    try:
        logger.info(f"Interview prep request from user: {request.user_id}")

        file, error = validate_file_upload(request)
        if error:
            return error

        file_path = save_uploaded_file(file)
        ext = get_file_extension(file.filename)
        text = extract_text_from_pdf(file_path) if ext == ".pdf" else extract_text_from_image(file_path)

        if not text.strip() or not text_looks_like_resume(text):
            logger.warning("File does not look like a resume")
            return jsonify({"error": "Invalid resume", "notResume": True}), 400

        job_desc = request.form.get("jobDescription", "general role")

        prompt = f"""Generate interview prep based on resume and job.

Resume: {text[:3000]}
Job: {job_desc}

Format as text (not JSON):
TECHNICAL QUESTIONS: (5 items)
BEHAVIORAL QUESTIONS: (5 items)
SYSTEM DESIGN: (3 items)
CODING TOPICS: (5 items)"""

        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
            timeout=30
        )

        logger.info("Interview prep generated successfully")
        return jsonify({"success": True, "interviewPrep": completion.choices[0].message.content})
    except Exception as e:
        logger.error(f"Interview prep error: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        cleanup_file(file_path)

@app.route("/generate-cover-letter", methods=["POST"])
@require_auth
def generate_cover_letter():
    """Generate cover letter"""
    file_path = None
    try:
        logger.info(f"Cover letter request from user: {request.user_id}")

        file, error = validate_file_upload(request)
        if error:
            return error

        job_desc = request.form.get("jobDescription", "").strip()
        has_valid, cleaned = validate_job_description(job_desc)

        if not has_valid:
            logger.warning("Invalid job description for cover letter")
            return jsonify({"error": "Provide valid job description", "needsJobDescription": True}), 400

        file_path = save_uploaded_file(file)
        ext = get_file_extension(file.filename)
        text = extract_text_from_pdf(file_path) if ext == ".pdf" else extract_text_from_image(file_path)

        if not text.strip() or not text_looks_like_resume(text):
            logger.warning("File does not look like a resume")
            return jsonify({"error": "Invalid resume", "notResume": True}), 400

        company = request.form.get("companyName", "the company")
        manager = request.form.get("hiringManager", "Hiring Manager")

        prompt = f"""Write professional cover letter.

Resume: {text[:3500]}
Job: {cleaned}
Company: {company}
Manager: {manager}

Start with "Dear {manager},"
End with "Sincerely,"
3-4 paragraphs, warm tone."""

        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            temperature=0.5,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
            timeout=30
        )

        logger.info("Cover letter generated successfully")
        return jsonify({"success": True, "coverLetter": completion.choices[0].message.content})
    except Exception as e:
        logger.error(f"Cover letter error: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        cleanup_file(file_path)

@app.route("/optimize-linkedin", methods=["POST"])
@require_auth
def optimize_linkedin():
    """Optimize LinkedIn profile based on resume"""
    file_path = None
    try:
        logger.info(f"LinkedIn optimization request from user: {request.user_id}")

        file, error = validate_file_upload(request)
        if error:
            return error

        file_path = save_uploaded_file(file)
        ext = get_file_extension(file.filename)
        text = extract_text_from_pdf(file_path) if ext == ".pdf" else extract_text_from_image(file_path)

        if not text.strip() or not text_looks_like_resume(text):
            logger.warning("File does not look like a resume")
            return jsonify({"error": "Invalid resume", "notResume": True}), 400

        target = request.form.get("targetRole") or request.form.get("jobDescription") or "general professional"

        prompt = f"""Generate LinkedIn content.

Resume: {text[:3500]}
Target: {target}

Return JSON (no markdown):
{{
  "headline": "220 chars max",
  "about": "summary with keywords",
  "experienceBullets": [{{"company": "", "role": "", "bullets": []}}],
  "featuredSkills": ["skill1"],
  "keywords": ["kw1"],
  "profileTips": ["tip1"]
}}"""

        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            temperature=0.3,
            messages=[
                {"role": "system", "content": "Return only JSON without markdown"},
                {"role": "user", "content": prompt}
            ],
            max_tokens=2500,
            timeout=30
        )

        parsed = extract_json_from_response(completion.choices[0].message.content)
        logger.info("LinkedIn optimization successful")
        return jsonify({"success": True, **parsed})
    except Exception as e:
        logger.error(f"LinkedIn optimization error: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        cleanup_file(file_path)

@app.route("/get-skill-resources", methods=["POST"])
@require_auth
def get_skill_resources():
    """Get comprehensive learning resources for missing skills"""
    try:
        logger.info(f"Skill resources request from user: {request.user_id}")

        data = request.get_json()
        missing_skills = data.get("missingSkills", [])
        skill_level = data.get("skillLevel", "beginner")

        if not missing_skills:
            logger.info("No missing skills provided")
            return jsonify({"success": True, "resources": []})

        logger.info(f"Finding resources for {len(missing_skills)} skills at {skill_level} level")
        print(f"🔍 Finding resources for {len(missing_skills)} skills at {skill_level} level...")

        # Limit to top 5 skills to avoid token limits
        top_skills = missing_skills[:5]

        prompt = f"""Generate comprehensive learning resources for a {skill_level} learner.

Skills: {', '.join(top_skills)}

Return STRICT JSON (no markdown):
{{
  "resources": [
    {{
      "skill": "skill name",
      "priority": "High",
      "difficulty": "Beginner",
      "estimatedTime": "2-3 weeks",
      "learningPath": {{
        "youtube": [
          {{"channel": "name", "title": "course", "url": "link", "duration": "20 hours", "subscribers": "1M", "isFree": true}}
        ],
        "courses": [
          {{"platform": "Udemy", "title": "Complete Guide", "instructor": "John Doe", "url": "link", "price": "$20", "rating": "4.5/5", "duration": "40 hours", "certificate": true, "isFree": false}}
        ],
        "practice": [
          {{"platform": "LeetCode", "type": "Coding Challenges", "url": "link", "description": "Practice problems", "isFree": true}}
        ]
      }},
      "projectIdeas": [
        {{"title": "Build a calculator", "description": "Create basic calculator", "difficulty": "Easy", "estimatedTime": "3 days"}}
      ],
      "tips": ["Start with fundamentals", "Practice daily", "Build projects"]
    }}
  ]
}}"""

        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            temperature=0.3,
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert career advisor. Return ONLY valid JSON without markdown. Recommend real, verified resources."
                },
                {"role": "user", "content": prompt}
            ],
            max_tokens=4000,
            timeout=30
        )

        raw = completion.choices[0].message.content
        logger.info(f"Resources response received ({len(raw)} chars)")
        print(f"🤖 Resources response (first 300 chars): {raw[:300]}...")

        parsed = extract_json_from_response(raw)
        resources = parsed.get("resources", [])

        logger.info(f"Found resources for {len(resources)} skills")
        print(f"✅ Found resources for {len(resources)} skills")

        result = {
            "success": True,
            "resources": resources,
            "overallRecommendations": parsed.get("overallRecommendations", {}),
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "skillCount": len(resources)
        }

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error getting resources: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/get-skill-roadmap", methods=["POST"])
@require_auth
def get_skill_roadmap():
    """Generate a detailed learning roadmap for a specific skill"""
    try:
        logger.info(f"Skill roadmap request from user: {request.user_id}")

        data = request.get_json()
        skill = data.get("skill", "").strip()
        current_level = data.get("currentLevel", "beginner")
        target_level = data.get("targetLevel", "professional")
        weekly_hours = data.get("weeklyHours", 10)

        if not skill:
            logger.warning("Skill name not provided")
            return jsonify({"error": "Skill name required"}), 400

        logger.info(f"Generating roadmap for {skill}")

        prompt = f"""Create detailed learning roadmap for: {skill}

Current: {current_level}
Target: {target_level}
Time: {weekly_hours} hours/week

Return JSON (no markdown):
{{
  "skill": "{skill}",
  "totalDuration": "3 months",
  "phases": [
    {{
      "phase": 1,
      "title": "Foundation",
      "duration": "4 weeks",
      "goals": ["goal1", "goal2"],
      "topics": [
        {{"name": "topic", "hours": 10, "resources": ["res1"], "milestones": ["checkpoint1"]}}
      ],
      "projects": [
        {{"name": "project", "description": "desc", "skills_practiced": ["skill1"]}}
      ]
    }}
  ],
  "milestones": [
    {{"week": 1, "checkpoint": "what you should know", "assessment": "how to test"}}
  ],
  "dailySchedule": {{
    "weekday": "1 hour practice",
    "weekend": "3 hours projects"
  }},
  "resources": {{
    "essential": ["must-have"],
    "supplementary": ["nice-to-have"]
  }},
  "successMetrics": ["metric1"]
}}"""

        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            temperature=0.3,
            messages=[
                {"role": "system", "content": "Create actionable roadmaps. Return only JSON without markdown."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=3000,
            timeout=30
        )

        parsed = extract_json_from_response(completion.choices[0].message.content)
        logger.info("Skill roadmap generated successfully")
        return jsonify({"success": True, **parsed})

    except Exception as e:
        logger.error(f"Error generating roadmap: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/track-learning-progress", methods=["POST"])
@require_auth
def track_learning_progress():
    """Track user's learning progress for skills"""
    try:
        logger.info(f"Track progress request from user: {request.user_id}")

        data = request.get_json()
        skill = data.get("skill")
        progress = data.get("progress", 0)  # 0-100
        completed_resources = data.get("completedResources", [])
        notes = data.get("notes", "")

        if not skill:
            logger.warning("Skill name not provided for progress tracking")
            return jsonify({"error": "Skill name required"}), 400

        learning_progress_collection.update_one(
            {
                "userId": request.user_id,
                "skill": skill
            },
            {
                "$set": {
                    "progress": progress,
                    "completedResources": completed_resources,
                    "notes": notes,
                    "lastUpdated": datetime.now(timezone.utc)
                },
                "$setOnInsert": {
                    "startedAt": datetime.now(timezone.utc)
                }
            },
            upsert=True
        )

        logger.info(f"Progress tracked for skill: {skill}")
        return jsonify({"success": True, "message": "Progress updated"})

    except Exception as e:
        logger.error(f"Track progress error: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/get-learning-progress", methods=["GET"])
@require_auth
def get_learning_progress():
    """Get user's learning progress"""
    try:
        logger.info(f"Get progress request from user: {request.user_id}")

        progress = list(learning_progress_collection.find(
            {"userId": request.user_id},
            {"_id": 0, "userId": 0}
        ).sort("lastUpdated", -1))

        for p in progress:
            if "startedAt" in p:
                p["startedAt"] = p["startedAt"].isoformat()
            if "lastUpdated" in p:
                p["lastUpdated"] = p["lastUpdated"].isoformat()

        logger.info(f"Retrieved {len(progress)} progress records")
        return jsonify({"success": True, "progress": progress})

    except Exception as e:
        logger.error(f"Get progress error: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/get-trending-skills", methods=["GET"])
@require_auth
def get_trending_skills():
    """Get trending skills in the industry"""
    try:
        logger.info(f"Trending skills request from user: {request.user_id}")

        industry = request.args.get("industry", "technology")

        prompt = f"""List top 20 trending skills for {industry} industry in 2024.

Return JSON (no markdown):
{{
  "trending": [
    {{
      "skill": "Python",
      "category": "Programming",
      "demandLevel": "Very High",
      "averageSalary": "$110,000",
      "growthRate": "25%",
      "jobOpenings": "50,000+",
      "difficulty": "Intermediate",
      "learningTime": "3-4 months",
      "relatedRoles": ["Developer", "Data Scientist"],
      "why_trending": "AI boom"
    }}
  ],
  "emergingSkills": ["AI", "Web3"],
  "decliningSkills": ["jQuery", "Flash"]
}}"""

        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            temperature=0.3,
            messages=[
                {"role": "system", "content": "Provide current job market data. Return only JSON without markdown."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=2500,
            timeout=30
        )

        parsed = extract_json_from_response(completion.choices[0].message.content)
        logger.info("Trending skills retrieved successfully")
        return jsonify({"success": True, **parsed})

    except Exception as e:
        logger.error(f"Error getting trending skills: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/save-history", methods=["POST"])
@require_auth
def save_history():
    """Save analysis history"""
    try:
        logger.info(f"Save history request from user: {request.user_id}")

        data = request.get_json()
        history_collection.insert_one({
            "userId": request.user_id,
            **data,
            "createdAt": datetime.now(timezone.utc)
        })
        logger.info("History saved successfully")
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Save history error: {e}")
        traceback.print_exc()
        return jsonify({"error": "Failed to save history"}), 500

@app.route("/history", methods=["GET"])
@require_auth
def get_history():
    """Get user's history"""
    try:
        logger.info(f"Get history request from user: {request.user_id}")

        records = list(history_collection.find(
            {"userId": request.user_id},
            {"_id": 0, "userId": 0}
        ).sort("createdAt", -1).limit(50))

        for r in records:
            if "createdAt" in r:
                r["createdAt"] = r["createdAt"].isoformat()

        logger.info(f"Retrieved {len(records)} history records")
        return jsonify(records)
    except Exception as e:
        logger.error(f"Get history error: {e}")
        traceback.print_exc()
        return jsonify([])

@app.route("/history/clear", methods=["DELETE"])
@require_auth
def clear_history():
    """Clear user's history"""
    try:
        logger.info(f"Clear history request from user: {request.user_id}")

        result = history_collection.delete_many({"userId": request.user_id})
        logger.info(f"Cleared {result.deleted_count} history records")
        return jsonify({"success": True, "deleted": result.deleted_count})
    except Exception as e:
        logger.error(f"Clear history error: {e}")
        traceback.print_exc()
        return jsonify({"error": "Failed to clear history"}), 500

@app.route("/generate-report", methods=["POST"])
@require_auth
def generate_report():
    """Generate PDF report"""
    try:
        logger.info(f"Generate report request from user: {request.user_id}")

        score = request.form.get("score", "N/A")
        fit_score = request.form.get("fitScore", "N/A")
        skills = safe_parse(request.form.get("skills"))
        matching = safe_parse(request.form.get("matchingSkills"))
        missing = safe_parse(request.form.get("missingSkills"))

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter)
        styles = getSampleStyleSheet()
        elements = []

        elements.append(Paragraph("Resume Analysis Report", styles['Title']))
        elements.append(Spacer(1, 12))
        elements.append(Paragraph(f"ATS Score: {score}%", styles['Normal']))
        elements.append(Paragraph(f"Fit Score: {fit_score}%", styles['Normal']))
        elements.append(Spacer(1, 12))
        elements.append(Paragraph("Matching Skills", styles['Heading2']))
        for skill in matching:
            elements.append(Paragraph(f"• {skill}", styles['Normal']))
        elements.append(Spacer(1, 12))
        elements.append(Paragraph("Missing Skills", styles['Heading2']))
        for skill in missing:
            elements.append(Paragraph(f"• {skill}", styles['Normal']))

        doc.build(elements)
        buffer.seek(0)

        logger.info("Report generated successfully")
        return send_file(
            buffer,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"resume_analysis_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        )
    except Exception as e:
        logger.error(f"Generate report error: {e}")
        traceback.print_exc()
        return jsonify({"error": "Failed to generate report"}), 500

# ===============================
# ERROR HANDLERS
# ===============================

@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors"""
    return jsonify({"error": "Endpoint not found"}), 404

@app.errorhandler(405)
def method_not_allowed(error):
    """Handle 405 errors"""
    return jsonify({"error": "Method not allowed"}), 405

@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors"""
    logger.error(f"Internal server error: {error}")
    return jsonify({"error": "Internal server error"}), 500

# ===============================
# RUN SERVER
# ===============================

if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    is_production = os.getenv("RENDER") is not None

    if is_production:
        print("🚀 Running in PRODUCTION mode on Render")
        print(f"📍 Port: {port}")
        logger.info("Starting app in PRODUCTION mode")
    else:
        print("🔧 Running in DEVELOPMENT mode")
        print(f"📍 http://localhost:{port}")
        logger.info("Starting app in DEVELOPMENT mode")

    app.run(
        host="0.0.0.0",
        port=port,
        debug=not is_production
    )