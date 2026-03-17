import os
import base64
import requests
from flask import Flask, request, jsonify, render_template
from PIL import Image
import io
import json

app = Flask(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
CANVA_API_KEY = os.environ.get("CANVA_API_KEY")
CANVA_DESIGN_ID = os.environ.get("CANVA_DESIGN_ID", "DAHCFrUWEbo")

SYSTEM_PROMPT = """Sen Mavi Zuccaciye için profesyonel Instagram stüdyo fotoğraf promptu yazan bir uzmansın.

Ürün fotoğrafını analiz et ve aşağıdaki kurallara göre Gemini Imagen için İngilizce prompt yaz:

KURALLAR:
1. MEKAN: Ürünün kullanım alanına göre otomatik seç (mutfak, oturma odası, yemek odası, araba içi, bahçe vb.)
2. IŞIK: Ürüne en uygun stüdyo ışığını kendin seç (soft diffused, warm natural window light vb.)
3. DEKOR STİLİ: Ürüne uygun stili kendin seç (modern minimal, rustik, İskandinav vb.)
4. DEKOR POZİSYON: Dekorlar SADECE blurlu arka planda olsun, ürünün yanında ASLA olmasın
5. ARKAPLAN: Tamamen bokeh/blur, ürün keskin odakta
6. ÜRÜN POZİSYON: Ekranın ortasının biraz üstünde, üstte şablon başlığı için boşluk kalsın, altta da biraz boşluk kalsın
7. YÜZEY: Modern, açık renkli (white marble veya light oak) - eski rustik masa değil
8. BOYUT: 1080x1920, 9:16 dikey format, Instagram Story

Sadece İngilizce prompt yaz, başka hiçbir şey yazma. Prompt tek paragraf olsun."""


def analyze_and_generate_prompt(image_base64: str, mime_type: str) -> str:
    """Gemini Vision ile ürünü analiz edip stüdyo promptu üret"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    
    payload = {
        "contents": [{
            "parts": [
                {
                    "inline_data": {
                        "mime_type": mime_type,
                        "data": image_base64
                    }
                },
                {
                    "text": "Bu ürün için stüdyo fotoğraf promptu üret."
                }
            ]
        }],
        "systemInstruction": {
            "parts": [{"text": SYSTEM_PROMPT}]
        },
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 500
        }
    }
    
    response = requests.post(url, json=payload)
    response.raise_for_status()
    
    data = response.json()
    prompt = data["candidates"][0]["content"]["parts"][0]["text"]
    return prompt.strip()


def generate_studio_image(prompt: str) -> str:
    """Gemini Imagen ile stüdyo görseli üret, base64 döndür"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-001:predict?key={GEMINI_API_KEY}"
    
    payload = {
        "instances": [{"prompt": prompt}],
        "parameters": {
            "sampleCount": 1,
            "aspectRatio": "9:16"
        }
    }
    
    response = requests.post(url, json=payload)
    response.raise_for_status()
    
    data = response.json()
    image_b64 = data["predictions"][0]["bytesBase64Encoded"]
    return image_b64


def upload_to_canva(image_b64: str, filename: str) -> str:
    """Görseli Canva'ya yükle, asset_id döndür"""
    # Base64'ü dosyaya çevir ve Canva'ya yükle
    image_data = base64.b64decode(image_b64)
    
    headers = {
        "Authorization": f"Bearer {CANVA_API_KEY}",
        "Content-Type": "application/octet-stream",
        "Asset-Upload-Metadata": json.dumps({
            "name": filename,
            "mime_type": "image/jpeg"
        })
    }
    
    response = requests.post(
        "https://api.canva.com/rest/v1/asset-uploads",
        headers=headers,
        data=image_data
    )
    response.raise_for_status()
    
    data = response.json()
    job_id = data["job"]["id"]
    
    # Upload tamamlanana kadar bekle
    import time
    for _ in range(30):
        time.sleep(2)
        status_response = requests.get(
            f"https://api.canva.com/rest/v1/asset-uploads/{job_id}",
            headers={"Authorization": f"Bearer {CANVA_API_KEY}"}
        )
        status_data = status_response.json()
        if status_data["job"]["status"] == "success":
            return status_data["job"]["asset"]["id"]
        elif status_data["job"]["status"] == "failed":
            raise Exception("Canva upload failed")
    
    raise Exception("Upload timeout")


def get_first_empty_page(design_id: str) -> dict:
    """Canva'daki ilk boş şablonu bul (Ürün ve Fiyat yazılı sayfa)"""
    headers = {"Authorization": f"Bearer {CANVA_API_KEY}"}
    
    # Tüm sayfaları al
    response = requests.get(
        f"https://api.canva.com/rest/v1/designs/{design_id}/pages",
        headers=headers
    )
    response.raise_for_status()
    data = response.json()
    
    # İçerikleri kontrol et - "Ürün ve Fiyat" yazılı ilk sayfayı bul
    content_response = requests.get(
        f"https://api.canva.com/rest/v1/designs/{design_id}",
        headers=headers,
        params={"content_types": "richtexts"}
    )
    
    # Basit yaklaşım: sayfa 17'den başla (bilinen boş şablonlar)
    pages = data.get("items", [])
    for page in pages:
        if page.get("index", 0) >= 17:
            return page
    
    return pages[-1] if pages else None


def add_image_to_canva_page(design_id: str, asset_id: str, page_index: int):
    """Canva API ile görseli belirtilen sayfaya ekle"""
    headers = {
        "Authorization": f"Bearer {CANVA_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Editing transaction başlat
    tx_response = requests.post(
        f"https://api.canva.com/rest/v1/designs/{design_id}/editing-sessions",
        headers=headers
    )
    tx_response.raise_for_status()
    tx_data = tx_response.json()
    session_id = tx_data["editing_session"]["id"]
    
    # Görseli sayfaya yerleştir
    edit_payload = {
        "changes": [{
            "operation": "set_page_background_image",
            "page_index": page_index,
            "asset_id": asset_id
        }]
    }
    
    edit_response = requests.post(
        f"https://api.canva.com/rest/v1/designs/{design_id}/editing-sessions/{session_id}/changes",
        headers=headers,
        json=edit_payload
    )
    
    # Commit
    requests.post(
        f"https://api.canva.com/rest/v1/designs/{design_id}/editing-sessions/{session_id}/publish",
        headers=headers
    )


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/process", methods=["POST"])
def process():
    try:
        if "image" not in request.files:
            return jsonify({"error": "Fotoğraf yüklenmedi"}), 400
        
        file = request.files["image"]
        image_data = file.read()
        mime_type = file.content_type or "image/jpeg"
        image_b64 = base64.b64encode(image_data).decode("utf-8")
        
        # 1. Gemini Vision ile prompt üret
        print("📝 Prompt üretiliyor...")
        prompt = analyze_and_generate_prompt(image_b64, mime_type)
        print(f"✅ Prompt: {prompt[:100]}...")
        
        # 2. Gemini Imagen ile stüdyo görseli üret
        print("🎨 Stüdyo görseli üretiliyor...")
        studio_image_b64 = generate_studio_image(prompt)
        print("✅ Görsel üretildi!")
        
        # 3. Canva'ya yükle
        print("📤 Canva'ya yükleniyor...")
        asset_id = upload_to_canva(studio_image_b64, f"studio_{file.filename}")
        print(f"✅ Asset ID: {asset_id}")
        
        return jsonify({
            "success": True,
            "prompt": prompt,
            "asset_id": asset_id,
            "image_preview": f"data:image/jpeg;base64,{studio_image_b64[:100]}...",
            "message": "Görsel Canva'ya yüklendi! Şimdi Claude'a asset_id'yi gönderin."
        })
        
    except Exception as e:
        print(f"❌ Hata: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
