from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import yt_dlp
import os
import tempfile
import uuid
import threading
import time

app = Flask(__name__)
CORS(app)

DOWNLOAD_DIR = tempfile.gettempdir()

# Cleanup old files every 10 minutes
def cleanup_old_files():
    while True:
        time.sleep(600)
        now = time.time()
        for f in os.listdir(DOWNLOAD_DIR):
            fpath = os.path.join(DOWNLOAD_DIR, f)
            if os.path.isfile(fpath) and now - os.path.getmtime(fpath) > 600:
                try:
                    os.remove(fpath)
                except Exception:
                    pass

threading.Thread(target=cleanup_old_files, daemon=True).start()


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "message": "Video Downloader API is running."})


@app.route("/info", methods=["POST"])
def get_info():
    data = request.get_json()
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"error": "No URL provided."}), 400

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return jsonify({
                "title": info.get("title", "Unknown"),
                "thumbnail": info.get("thumbnail", ""),
                "duration": info.get("duration", 0),
                "uploader": info.get("uploader", "Unknown"),
                "platform": info.get("extractor_key", "Unknown"),
                "formats": [
                    {
                        "format_id": f.get("format_id"),
                        "ext": f.get("ext"),
                        "resolution": f.get("resolution") or f.get("height", "audio only"),
                        "filesize": f.get("filesize") or f.get("filesize_approx"),
                        "note": f.get("format_note", ""),
                    }
                    for f in info.get("formats", [])
                    if f.get("ext") in ("mp4", "webm", "m4a", "mp3")
                ],
            })
    except yt_dlp.utils.DownloadError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500


@app.route("/download", methods=["POST"])
def download_video():
    data = request.get_json()
    url = data.get("url", "").strip()
    quality = data.get("quality", "best")   # "best", "worst", "audio"
    fmt = data.get("format", "mp4")

    if not url:
        return jsonify({"error": "No URL provided."}), 400

    file_id = str(uuid.uuid4())
    output_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")

    if quality == "audio":
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": output_path,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "quiet": True,
        }
        expected_ext = "mp3"
    else:
        format_str = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best" if quality == "best" else "worst[ext=mp4]/worst"
        ydl_opts = {
            "format": format_str,
            "outtmpl": output_path,
            "merge_output_format": "mp4",
            "quiet": True,
        }
        expected_ext = "mp4"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "video")

        # Find the downloaded file
        downloaded = None
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(file_id):
                downloaded = os.path.join(DOWNLOAD_DIR, f)
                break

        if not downloaded or not os.path.exists(downloaded):
            return jsonify({"error": "Download failed — file not found."}), 500

        safe_title = "".join(c for c in title if c.isalnum() or c in " _-").strip()[:60]
        ext = os.path.splitext(downloaded)[1]
        download_name = f"{safe_title}{ext}"

        return send_file(
            downloaded,
            as_attachment=True,
            download_name=download_name,
            mimetype="audio/mpeg" if ext == ".mp3" else "video/mp4",
        )

    except yt_dlp.utils.DownloadError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
