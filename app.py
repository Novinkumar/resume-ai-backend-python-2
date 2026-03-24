# app.py
print("🔥🔥🔥 RUNNING THIS APP.PY FILE 🔥🔥🔥")

import os
import json
import re
import uuid
import traceback
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
import easyocr

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
# EASYOCR SETUP
# ===============================
print("🔄 Initializing EasyOCR (may take 1-2 minutes first time)...")
try:
    ocr_reader = easyocr.Reader(['en'], gpu=False, verbose=False)
    print("✅ EasyOCR initialized")
except Exception as e:
    print(f"❌ EasyOCR initialization failed: {e}")
    ocr_reader = None

# ===============================
# FLASK APP
# ===============================
app = Flask(__name__)

# 🔥 UPDATED CORS FOR PRODUCTION
ALLOWED_ORIGINS = [
    "http://localhost:*",
    "http://127.0.0.1:*",
    "http://10.0.2.2:*",
    "https://your-app-name.onrender.com",  # Replace with actual Render URL
    "*"  # Allow all for now, restrict later
]

CORS(app, resources={
    r"/*": {
        "origins": "*",  # Change to ALLOWED_ORIGINS after testing
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
    if not text or len(text.strip()) < 100:
        return False
    text_lower = text.lower()
    matches = [kw for kw in RESUME_KEYWORDS if kw in text_lower]
    count = len(matches)
    min_required = 5 if strict else 3
    return count >= min_required

def validate_job_description(job_desc):
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
    if not val:
        return []
    if isinstance(val, list):
        return val
    try:
        return json.loads(val)
    except:
        return []

def allowed_file(filename):
    return os.path.splitext(filename)[1].lower() in ALLOWED_EXTENSIONS

def get_file_extension(filename):
    return os.path.splitext(filename)[1].lower()

def is_image_file(filename):
    return get_file_extension(filename) in {".png", ".jpg", ".jpeg", ".gif", ".webp"}

def extract_text_from_pdf(file_path):
    try:
        reader = PdfReader(file_path)
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as e:
        print(f"❌ PDF error: {e}")
        return ""

def extract_text_from_image(file_path):
    """Extract text using EasyOCR"""
    if not ocr_reader:
        raise Exception("OCR not initialized")

    try:
        print("🔍 Using EasyOCR...")
        result = ocr_reader.readtext(file_path, detail=0, paragraph=True)
        text = '\n'.join(result)
        print(f"✅ Extracted {len(text)} chars")
        return text
    except Exception as e:
        print(f"❌ OCR error: {e}")
        raise

def save_uploaded_file(file):
    ext = get_file_extension(file.filename)
    unique_name = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(UPLOAD_DIR, unique_name)
    file.save(file_path)
    return file_path

def cleanup_file(file_path):
    try:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
    except:
        pass

def extract_json_from_response(raw_text):
    """Extract JSON from AI response - handles markdown code blocks"""
    # Remove markdown code blocks if present
    cleaned = re.sub(r'```json\s*', '', raw_text)
    cleaned = re.sub(r'```\s*$', '', cleaned)

    # Try to find JSON object
    match = re.search(r'\{[\s\S]*\}', cleaned)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as e:
            print(f"❌ JSON decode error: {e}")
            print(f"Raw text: {raw_text[:500]}")
            raise ValueError(f"Invalid JSON in response: {e}")
    raise ValueError("No JSON found in response")

def validate_file_upload(request_obj):
    if "resume" not in request_obj.files:
        return None, (jsonify({"error": "No file uploaded"}), 400)
    file = request_obj.files["resume"]
    if not file.filename or not allowed_file(file.filename):
        return None, (jsonify({"error": "Invalid file type"}), 400)
    return file, None

# ===============================
# SKILL LEARNING RESOURCES
# ===============================
def get_quick_learning_tips(skills):
    """Get quick learning tips for top missing skills"""
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
            "kotlin": {"platform": "Kotlin for Android, Udemy", "time": "3-4 weeks", "type": "Mobile"},
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

            # Try exact match first
            matched = False
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

        return tips
    except Exception as e:
        print(f"⚠️ Error in get_quick_learning_tips: {e}")
        traceback.print_exc()
        return []


def get_detailed_skill_resources(skills):
    """Get detailed learning resources using AI for top missing skills"""
    try:
        if not skills or len(skills) == 0:
            return []

        # Limit to top 3 to avoid token limits
        top_skills = skills[:3]

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
            max_tokens=2500
        )

        raw_response = completion.choices[0].message.content
        print(f"🤖 AI Resources Response (first 300 chars): {raw_response[:300]}...")

        parsed = extract_json_from_response(raw_response)
        resources = parsed.get("resources", [])

        return resources

    except Exception as e:
        print(f"⚠️ Error in get_detailed_skill_resources: {e}")
        traceback.print_exc()
        return []

# ===============================
# ROUTES
# ===============================
@app.route("/", methods=["GET"])
def root():
    return "Resume Analyzer Backend Running ✅"

@app.route("/health", methods=["GET"])
def health_check():
    try:
        mongo_client.admin.command("ping")
        db_status = "connected"
    except:
        db_status = "disconnected"
    return jsonify({
        "status": "running",
        "database": db_status,
        "ai": "groq",
        "ocr": "easyocr"
    })

@app.route("/register", methods=["POST"])
def register():
    try:
        data = request.get_json()
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")

        if not email or len(password) < 6:
            return jsonify({"error": "Invalid email or password"}), 400

        if users_collection.find_one({"email": email}):
            return jsonify({"error": "User exists"}), 400

        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
        users_collection.insert_one({
            "email": email,
            "password": hashed.decode(),
            "createdAt": datetime.now(timezone.utc)
        })
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/login", methods=["POST"])
def login():
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

        return jsonify({"success": True, "token": token})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/analyze", methods=["POST"])
@require_auth
def analyze():
    file_path = None
    try:
        print("\n" + "="*70)
        print("📥 ANALYZE REQUEST")
        print("="*70)
        print(f"User ID: {request.user_id}")
        print(f"Files received: {request.files}")
        print(f"Form data: {request.form}")

        file, error = validate_file_upload(request)
        if error:
            return error

        job_description = request.form.get("jobDescription", "").strip()
        file_path = save_uploaded_file(file)

        ext = get_file_extension(file.filename)
        is_image = is_image_file(file.filename)

        print(f"📄 File extension: {ext}")
        print(f"📄 Is image: {is_image}")
        print(f"📄 File path: {file_path}")

        # Extract text
        if ext == ".pdf":
            print("📑 Processing PDF...")
            text = extract_text_from_pdf(file_path)
            print(f"📑 PDF text length: {len(text)} characters")
            print(f"📑 First 200 chars: {text[:200] if text else 'EMPTY'}")
        elif is_image:
            print("🖼️ Processing Image...")
            text = extract_text_from_image(file_path)
            print(f"🖼️ Image text length: {len(text)} characters")
        else:
            return jsonify({"error": "Invalid file type"}), 400

        # Validate text
        if not text.strip() or len(text.strip()) < 100:
            print(f"❌ Not enough text extracted: {len(text.strip())} chars")
            return jsonify({
                "error": f"Not enough text extracted ({len(text.strip())} chars). Upload clearer file.",
                "notResume": True
            }), 400

        print(f"✅ Text validated: {len(text)} chars")
        print("✅ Proceeding to analysis...")

        return analyze_with_text(text, job_description)

    except Exception as e:
        print(f"❌ Error: {e}")
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500
    finally:
        cleanup_file(file_path)


def analyze_with_text(text, job_description):
    """Analyze resume text and return results with learning resources"""
    has_valid_job, cleaned_job = validate_job_description(job_description)

    if not has_valid_job:
        prompt = f"""Analyze this resume (no job description provided).

Resume: {text[:4000]}

Return STRICT JSON (no markdown):
{{
  "atsScore": 75,
  "fitScore": 0,
  "skillStrength": {{}},
  "matchingSkills": [],
  "missingSkills": [],
  "aiDetection": {{"aiProbability": 0, "riskLevel": "Low", "flaggedSections": [], "reasons": [], "suggestions": []}},
  "overallFeedback": "2-3 sentences"
}}"""
    else:
        prompt = f"""Analyze resume vs job description.

Resume: {text[:4000]}

Job: {cleaned_job}

Return STRICT JSON (no markdown):
{{
  "atsScore": 75,
  "fitScore": 68,
  "skillStrength": {{"Python": 8, "SQL": 7}},
  "matchingSkills": ["Python", "SQL"],
  "missingSkills": ["Docker", "AWS"],
  "aiDetection": {{"aiProbability": 15, "riskLevel": "Low", "flaggedSections": [], "reasons": [], "suggestions": []}},
  "overallFeedback": "2-3 sentences"
}}"""

    completion = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        temperature=0.2,
        messages=[
            {"role": "system", "content": "Return only valid JSON without markdown code blocks"},
            {"role": "user", "content": prompt}
        ],
        max_tokens=2000
    )

    parsed = extract_json_from_response(completion.choices[0].message.content)

    if not has_valid_job:
        parsed["fitScore"] = 0
        parsed["matchingSkills"] = []
        parsed["missingSkills"] = []

    # ✅ Add learning resources for missing skills
    missing_skills = parsed.get("missingSkills", [])
    if missing_skills and len(missing_skills) > 0:
        print(f"🎓 Generating learning resources for {len(missing_skills)} missing skills...")

        try:
            # Get quick learning tips (fast, from database)
            parsed["skillResourcesAvailable"] = True
            quick_tips = get_quick_learning_tips(missing_skills[:5])
            if quick_tips:
                parsed["quickLearningTips"] = quick_tips
                print(f"✅ Added {len(quick_tips)} quick learning tips")

            # Get detailed resources for top 3 skills (AI-powered)
            detailed_resources = get_detailed_skill_resources(missing_skills[:3])
            if detailed_resources:
                parsed["learningResources"] = detailed_resources
                print(f"✅ Added detailed learning resources for {len(detailed_resources)} skills")
            else:
                print("⚠️ No detailed resources generated")

        except Exception as e:
            print(f"⚠️ Could not generate learning resources: {e}")
            traceback.print_exc()
            # Don't fail the entire request if resource generation fails
            parsed["skillResourcesAvailable"] = False
    else:
        print("ℹ️ No missing skills found, skipping resource generation")

    return jsonify({"success": True, **parsed})


@app.route("/optimize-resume", methods=["POST"])
@require_auth
def optimize_resume():
    file_path = None
    try:
        file, error = validate_file_upload(request)
        if error:
            return error

        job_desc = request.form.get("jobDescription", "").strip()
        has_valid, cleaned = validate_job_description(job_desc)

        if not has_valid:
            return jsonify({"error": "Provide valid job description", "needsJobDescription": True}), 400

        file_path = save_uploaded_file(file)
        ext = get_file_extension(file.filename)

        text = extract_text_from_pdf(file_path) if ext == ".pdf" else extract_text_from_image(file_path)

        if not text.strip() or not text_looks_like_resume(text, strict=True):
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
            ]
        )

        parsed = extract_json_from_response(completion.choices[0].message.content)
        return jsonify({"success": True, **parsed})
    except Exception as e:
        print(f"❌ Optimize resume error: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        cleanup_file(file_path)

@app.route("/interview", methods=["POST"])
@require_auth
def interview():
    file_path = None
    try:
        file, error = validate_file_upload(request)
        if error:
            return error

        file_path = save_uploaded_file(file)
        ext = get_file_extension(file.filename)
        text = extract_text_from_pdf(file_path) if ext == ".pdf" else extract_text_from_image(file_path)

        if not text.strip() or not text_looks_like_resume(text):
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
            messages=[{"role": "user", "content": prompt}]
        )

        return jsonify({"success": True, "interviewPrep": completion.choices[0].message.content})
    except Exception as e:
        print(f"❌ Interview prep error: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        cleanup_file(file_path)

@app.route("/generate-cover-letter", methods=["POST"])
@require_auth
def generate_cover_letter():
    file_path = None
    try:
        file, error = validate_file_upload(request)
        if error:
            return error

        job_desc = request.form.get("jobDescription", "").strip()
        has_valid, cleaned = validate_job_description(job_desc)

        if not has_valid:
            return jsonify({"error": "Provide valid job description", "needsJobDescription": True}), 400

        file_path = save_uploaded_file(file)
        ext = get_file_extension(file.filename)
        text = extract_text_from_pdf(file_path) if ext == ".pdf" else extract_text_from_image(file_path)

        if not text.strip() or not text_looks_like_resume(text):
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
            max_tokens=1500
        )

        return jsonify({"success": True, "coverLetter": completion.choices[0].message.content})
    except Exception as e:
        print(f"❌ Cover letter error: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        cleanup_file(file_path)

@app.route("/optimize-linkedin", methods=["POST"])
@require_auth
def optimize_linkedin():
    file_path = None
    try:
        file, error = validate_file_upload(request)
        if error:
            return error

        file_path = save_uploaded_file(file)
        ext = get_file_extension(file.filename)
        text = extract_text_from_pdf(file_path) if ext == ".pdf" else extract_text_from_image(file_path)

        if not text.strip() or not text_looks_like_resume(text):
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
            max_tokens=2500
        )

        parsed = extract_json_from_response(completion.choices[0].message.content)
        return jsonify({"success": True, **parsed})
    except Exception as e:
        print(f"❌ LinkedIn optimization error: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    finally:
        cleanup_file(file_path)

@app.route("/get-skill-resources", methods=["POST"])
@require_auth
def get_skill_resources():
    """Get comprehensive learning resources for missing skills"""
    try:
        data = request.get_json()
        missing_skills = data.get("missingSkills", [])
        skill_level = data.get("skillLevel", "beginner")

        if not missing_skills:
            return jsonify({"success": True, "resources": []})

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
        ],
        "certifications": [
          {{"name": "Cert Name", "provider": "Provider", "cost": "$100", "duration": "2 months", "industry_value": "High"}}
        ],
        "books": [
          {{"title": "Book Title", "author": "Author", "type": "Free PDF", "url": "link"}}
        ]
      }},
      "projectIdeas": [
        {{"title": "Build a calculator", "description": "Create basic calculator", "difficulty": "Easy", "estimatedTime": "3 days", "technologies": ["HTML", "CSS", "JS"]}}
      ],
      "studyPlan": {{
        "week1": "Learn basics",
        "week2": "Practice fundamentals",
        "week3": "Build projects",
        "week4": "Advanced concepts"
      }},
      "tips": ["Start with fundamentals", "Practice daily", "Build projects"],
      "relatedSkills": ["skill1", "skill2"]
    }}
  ],
  "overallRecommendations": {{
    "priorityOrder": ["skill1", "skill2"],
    "totalEstimatedTime": "3 months",
    "budgetFriendly": true,
    "learningStrategy": "Focus on one skill at a time"
  }}
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
            max_tokens=4000
        )

        raw = completion.choices[0].message.content
        print(f"🤖 Resources response (first 300 chars): {raw[:300]}...")

        parsed = extract_json_from_response(raw)
        resources = parsed.get("resources", [])

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
        print(f"❌ Error getting resources: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/get-skill-roadmap", methods=["POST"])
@require_auth
def get_skill_roadmap():
    """Generate a detailed learning roadmap for a specific skill"""
    try:
        data = request.get_json()
        skill = data.get("skill", "").strip()
        current_level = data.get("currentLevel", "beginner")
        target_level = data.get("targetLevel", "professional")
        weekly_hours = data.get("weeklyHours", 10)

        if not skill:
            return jsonify({"error": "Skill name required"}), 400

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
            max_tokens=3000
        )

        parsed = extract_json_from_response(completion.choices[0].message.content)
        return jsonify({"success": True, **parsed})

    except Exception as e:
        print(f"❌ Error generating roadmap: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/track-learning-progress", methods=["POST"])
@require_auth
def track_learning_progress():
    """Track user's learning progress for skills"""
    try:
        data = request.get_json()
        skill = data.get("skill")
        progress = data.get("progress", 0)  # 0-100
        completed_resources = data.get("completedResources", [])
        notes = data.get("notes", "")

        if not skill:
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

        return jsonify({"success": True, "message": "Progress updated"})

    except Exception as e:
        print(f"❌ Track progress error: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/get-learning-progress", methods=["GET"])
@require_auth
def get_learning_progress():
    """Get user's learning progress"""
    try:
        progress = list(learning_progress_collection.find(
            {"userId": request.user_id},
            {"_id": 0, "userId": 0}
        ).sort("lastUpdated", -1))

        for p in progress:
            if "startedAt" in p:
                p["startedAt"] = p["startedAt"].isoformat()
            if "lastUpdated" in p:
                p["lastUpdated"] = p["lastUpdated"].isoformat()

        return jsonify({"success": True, "progress": progress})

    except Exception as e:
        print(f"❌ Get progress error: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/get-trending-skills", methods=["GET"])
@require_auth
def get_trending_skills():
    """Get trending skills in the industry"""
    try:
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
            max_tokens=2500
        )

        parsed = extract_json_from_response(completion.choices[0].message.content)
        return jsonify({"success": True, **parsed})

    except Exception as e:
        print(f"❌ Error getting trending skills: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/save-history", methods=["POST"])
@require_auth
def save_history():
    try:
        data = request.get_json()
        history_collection.insert_one({
            "userId": request.user_id,
            **data,
            "createdAt": datetime.now(timezone.utc)
        })
        return jsonify({"success": True})
    except Exception as e:
        print(f"❌ Save history error: {e}")
        traceback.print_exc()
        return jsonify({"error": "Failed"}), 500

@app.route("/history", methods=["GET"])
@require_auth
def get_history():
    try:
        records = list(history_collection.find(
            {"userId": request.user_id},
            {"_id": 0, "userId": 0}
        ).sort("createdAt", -1).limit(50))

        for r in records:
            if "createdAt" in r:
                r["createdAt"] = r["createdAt"].isoformat()
        return jsonify(records)
    except Exception as e:
        print(f"❌ Get history error: {e}")
        traceback.print_exc()
        return jsonify([])

@app.route("/history/clear", methods=["DELETE"])
@require_auth
def clear_history():
    try:
        result = history_collection.delete_many({"userId": request.user_id})
        return jsonify({"success": True, "deleted": result.deleted_count})
    except Exception as e:
        print(f"❌ Clear history error: {e}")
        traceback.print_exc()
        return jsonify({"error": "Failed"}), 500

@app.route("/generate-report", methods=["POST"])
@require_auth
def generate_report():
    try:
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

        doc.build(elements)
        buffer.seek(0)

        return send_file(buffer, mimetype="application/pdf", as_attachment=True, download_name="report.pdf")
    except Exception as e:
        print(f"❌ Generate report error: {e}")
        traceback.print_exc()
        return jsonify({"error": "Failed"}), 500

# ===============================
# RUN SERVER
# ===============================
if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))

    # 🔥 PRODUCTION CHECK
    is_production = os.getenv("RENDER") is not None

    if is_production:
        print("🚀 Running in PRODUCTION mode on Render")
        print(f"📍 Port: {port}")
    else:
        print("🔧 Running in DEVELOPMENT mode")
        print(f"📍 http://localhost:{port}")

    # 🔥 Use gunicorn in production, Flask dev server locally
    app.run(
        host="0.0.0.0",
        port=port,
        debug=not is_production
    )