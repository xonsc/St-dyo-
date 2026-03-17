import os
import base64
import requests
import time
import json
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
CANVA_API_KEY = os.environ.get("CANVA_API_KEY")
CANVA_DESIGN_ID = os.environ.get("CANVA_DESIGN_ID", "DAHCFrUWEbo")

SYSTEM_PROMPT = """You are a professional Instagram studio photography prompt writer for Mavi Zuccaciye, a home and kitchen brand.

Analyze the product in the image and write a single English prompt for Gemini Imagen following these rules:

1. LOCATION: Choose the most fitting room based on the product (kitchen, living room, dining room, bathroom, car interior, garden etc.)
2. LIGHTING: Choose the best professional studio lighting that suits the product naturally
3. DECORATION STYLE: Choose a fitting style based on the product (modern minimal, Scandinavian, rustic etc.)
4. DECORATION POSITION: Decor elements ONLY in the blurred background, NEVER beside or in front of the product
5. BACKGROUND: Heavily blurred bokeh, product in sharp focus
6. PRODUCT POSITION: Slightly above center of frame, generous empty space at top for brand header overlay, small space at bottom
7. SURFACE: Modern light-colored surface (white marble or light oak) - NOT old rustic wood
8. SIZE: 1080x1920, 9:16 vertical format, Instagram Story

Write ONLY the English prompt as a single paragraph. Nothing else."""


def gemini_request(url, payload, retries=3):
    for attempt in range(retries):
        try:
            response = requests.post(url, json=payload, timeout=60)
            if response.status_code == 429:
                wait = 30 * (attempt + 1)
                print(f"429 Rate limit, {wait}s bekleniyor...")
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response
        except requests.exceptions.Timeout:
            if attempt < retries - 1:
                time.sleep(10)
                continue
            raise
    raise Exception("Gemini API rate limit - lutfen 1 dakika sonra tekrar dene")


def analyze_and_generate_prompt(image_base64, mime_type):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-001:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [
            {"inline_data": {"mime_type": mime_type, "data": image_base64}},
            {"text": "Analyze this product and write a studio photography prompt."}
        ]}],
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 500}
    }
    response = gemini_request(url, payload)
    data = response.json()
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


def generate_studio_image(prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-001:predict?key={GEMINI_API_KEY}"
    payload = {
        "instances": [{"prompt": prompt}],
        "parameters": {"sampleCount": 1, "aspectRatio": "9:16"}
    }
    response = gemini_request(url, payload)
    data = response.json()
    return data["predictions"][0]["bytesBase64Encoded"]


def upload_to_canva(image_b64, filename):
    image_data = base64.b64decode(image_b64)
    headers = {
        "Authorization": f"Bearer {CANVA_API_KEY}",
        "Content-Type": "application/octet-stream",
        "Asset-Upload-Metadata": json.dumps({"name": filename, "mime_type": "image/jpeg"})
    }
    response = requests.post("https://api.canva.com/rest/v1/asset-uploads", headers=headers, data=image_data, timeout=60)
    response.raise_for_status()
    job_id = response.json()["job"]["id"]

    for _ in range(30):
        time.sleep(2)
        status = requests.get(f"https://api.canva.com/rest/v1/asset-uploads/{job_id}", headers={"Authorization": f"Bearer {CANVA_API_KEY}"}, timeout=30).json()
        if status["job"]["status"] == "success":
            return status["job"]["asset"]["id"]
        elif status["job"]["status"] == "failed":
            raise Exception("Canva upload basarisiz")
    raise Exception("Canva upload timeout")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/process", methods=["POST"])
def process():
    try:
        if "image" not in request.files:
            return jsonify({"error": "Fotograf yuklenmedi"}), 400

        file = request.files["image"]
        image_data = file.read()
        mime_type = file.content_type or "image/jpeg"
        image_b64 = base64.b64encode(image_data).decode("utf-8")

        print("Prompt uretiliyor...")
        prompt = analyze_and_generate_prompt(image_b64, mime_type)
        print(f"Prompt: {prompt[:80]}...")

        print("Studio gorseli uretiliyor...")
        studio_image_b64 = generate_studio_image(prompt)
        print("Gorsel uretildi!")

        print("Canvaya yukleniyor...")
        filename = f"studio_{int(time.time())}.jpg"
        asset_id = upload_to_canva(studio_image_b64, filename)
        print(f"Asset ID: {asset_id}")

        return jsonify({
            "success": True,
            "prompt": prompt,
            "asset_id": asset_id,
            "studio_image": f"data:image/jpeg;base64,{studio_image_b64}",
            "message": "Gorsel Canvaya yuklendi!"
        })

    except Exception as e:
        print(f"Hata: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
