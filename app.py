import io
import os
import tempfile
import zipfile
from deep_translator import GoogleTranslator
from faster_whisper import WhisperModel
from flask import Flask, jsonify, render_template_string, request, send_file
import pykakasi

app = Flask(__name__)

kks = pykakasi.kakasi()

# Load Whisper model in INT8 mode for <8GB RAM CPU
whisper_model = WhisperModel("small", device="cpu", compute_type="int8")
translator = GoogleTranslator(source="ja", target="en")


def format_timestamp(seconds: float) -> str:
    """Converts seconds into SRT timestamp format: HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def get_romaji(japanese_text: str) -> str:
    """Converts Kanji/Kana into Romaji pronunciation"""
    result = kks.convert(japanese_text)
    return " ".join([item["hepburn"] for item in result if item["hepburn"]])


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Japanese MP3 Subtitle Generator</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #0f172a;
            color: #f8fafc;
            display: flex;
            justify-content: center;
            padding: 40px 20px;
        }
        .container {
            width: 100%;
            max-width: 680px;
            background: #1e293b;
            padding: 30px;
            border-radius: 12px;
            box-shadow: 0 10px 25px rgba(0,0,0,0.3);
        }
        h1 { margin-top: 0; font-size: 1.5rem; color: #38bdf8; }
        p { color: #94a3b8; font-size: 0.9rem; line-height: 1.5; }
        .upload-box {
            border: 2px dashed #475569;
            border-radius: 8px;
            padding: 30px;
            text-align: center;
            margin: 20px 0;
            background: #0f172a;
        }
        input[type="file"] { display: none; }
        label {
            background: #38bdf8;
            color: #0f172a;
            padding: 10px 20px;
            border-radius: 6px;
            font-weight: 600;
            cursor: pointer;
            display: inline-block;
        }
        button {
            width: 100%;
            background: #22c55e;
            color: white;
            border: none;
            padding: 12px;
            border-radius: 6px;
            font-size: 1rem;
            font-weight: 600;
            cursor: pointer;
        }
        button:disabled { background: #475569; cursor: not-allowed; }
        #status { margin-top: 15px; font-weight: 500; text-align: center; }
        .file-list {
            margin-top: 15px;
            padding: 12px;
            background: #0f172a;
            border-radius: 6px;
            font-size: 0.85rem;
            color: #94a3b8;
        }
    </style>
</head>
<body>
<div class="container">
    <h1>JP to ENG Subtitle as SRT file</h1>
    <p>Generates 3 separate SRT files (JP, Romaji, EN) packed into a ZIP archive.</p>

    <div class="upload-box">
        <label for="audioFile">Choose Audio File</label>
        <input type="file" id="audioFile" accept=".mp3,.wav,.m4a,.ogg">
        <p id="fileName" style="margin-top: 10px;">No file selected</p>
    </div>

    <button id="processBtn" disabled onclick="processAudio()">Generate SRTs (.zip)</button>
    <div id="status"></div>

    <div class="file-list">
        <strong>Included in output archive:</strong>
        <ul style="margin: 5px 0 0 0; padding-left: 20px;">
            <li><code>[filename]_JP.srt</code> (Original Japanese text)</li>
            <li><code>[filename]_Romaji.srt</code> (Pronunciation transcript)</li>
            <li><code>[filename]_EN.srt</code> (English translation)</li>
        </ul>
    </div>
</div>

<script>
    const fileInput = document.getElementById('audioFile');
    const fileName = document.getElementById('fileName');
    const processBtn = document.getElementById('processBtn');
    const statusDiv = document.getElementById('status');

    fileInput.addEventListener('change', () => {
        if (fileInput.files.length > 0) {
            fileName.innerText = fileInput.files[0].name;
            processBtn.disabled = false;
        }
    });

    async function processAudio() {
        const file = fileInput.files[0];
        if (!file) return;

        const formData = new FormData();
        formData.append('audio', file);

        processBtn.disabled = true;
        statusDiv.style.color = '#38bdf8';
        statusDiv.innerText = 'Processing...';

        try {
            const response = await fetch('/generate-srt', {
                method: 'POST',
                body: formData
            });

            if (!response.ok) throw new Error('Generation failed');

            const blob = await response.blob();
            const baseName = file.name.replace(/\.[^/.]+$/, "");

            const downloadUrl = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = downloadUrl;
            a.download = `${baseName}_subtitles.zip`;
            document.body.appendChild(a);
            a.click();
            a.remove();

            statusDiv.style.color = '#22c55e';
            statusDiv.innerText = 'Complete! ZIP archive downloaded.';
        } catch (err) {
            statusDiv.style.color = '#ef4444';
            statusDiv.innerText = 'Error: ' + err.message;
        } finally {
            processBtn.disabled = false;
        }
    }
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/generate-srt", methods=["POST"])
def generate_srt():
    if "audio" not in request.files:
        return jsonify({"error": "No audio file provided"}), 400

    audio_file = request.files["audio"]
    base_name = os.path.splitext(audio_file.filename)[0]

    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as temp_audio:
        audio_file.save(temp_audio.name)
        temp_audio_path = temp_audio.name

    try:
        # Transcribe without discarding any acoustic frequencies
        segments, _ = whisper_model.transcribe(
            temp_audio_path,
            language="ja",
            beam_size=5,
            word_timestamps=True,
            vad_filter=False,
            condition_on_previous_text=False,
            no_speech_threshold=None,
            log_prob_threshold=None,
            temperature=0.0,
        )

        raw_segments = list(segments)
        if not raw_segments:
            return jsonify({"error": "No speech detected"}), 400

        # Filter out empty or whitespace-only lines
        valid_segments = [s for s in raw_segments if s.text.strip()]
        if not valid_segments:
            return jsonify({"error": "No speech detected"}), 400

        ja_lines = [s.text.strip() for s in valid_segments]

        # Batch translate to English
        chunk_size = 50
        en_translations = []
        for i in range(0, len(ja_lines), chunk_size):
            chunk = ja_lines[i : i + chunk_size]
            try:
                translated_chunk = translator.translate_batch(chunk)
                en_translations.extend(translated_chunk)
            except Exception:
                en_translations.extend(["[Translation Unavailable]"] * len(chunk))

        timed_subs = []
        punctuation_set = set("、。！？,.!?…〜ー- ")

        for seg, ja_text, en_text in zip(
            valid_segments, ja_lines, en_translations
        ):
            # Isolate actual spoken words, excluding punctuation tokens
            meaningful_words = [
                w
                for w in (seg.words or [])
                if w.word.strip() and not all(c in punctuation_set for c in w.word.strip())
            ]

            if meaningful_words:
                first_word = meaningful_words[0]
                last_word = meaningful_words[-1]

                # Prevent Whisper from stretching initial word backwards into silence
                word_len = len(first_word.word.strip())
                max_expected_duration = max(0.4, word_len * 0.25)

                if (first_word.end - first_word.start) > max_expected_duration + 0.5:
                    start_time = max(first_word.start, first_word.end - max_expected_duration)
                else:
                    start_time = first_word.start

                end_time = last_word.end + 0.8
            else:
                start_time = seg.start
                end_time = seg.end + 0.8

            timed_subs.append(
                {
                    "start": start_time,
                    "end": end_time,
                    "ja": ja_text,
                    "romaji": get_romaji(ja_text),
                    "en": en_text,
                }
            )

        # Anti-Collision: keep 100ms space between sequential subtitles
        for i in range(len(timed_subs) - 1):
            curr_sub = timed_subs[i]
            next_sub = timed_subs[i + 1]

            if curr_sub["end"] >= next_sub["start"]:
                curr_sub["end"] = max(
                    curr_sub["start"] + 0.4, next_sub["start"] - 0.1
                )

        jp_entries = []
        romaji_entries = []
        en_entries = []

        for idx, sub in enumerate(timed_subs, start=1):
            start_str = format_timestamp(sub["start"])
            end_str = format_timestamp(sub["end"])

            jp_entries.append(
                f"{idx}\n{start_str} --> {end_str}\n{sub['ja']}\n"
            )
            romaji_entries.append(
                f"{idx}\n{start_str} --> {end_str}\n{sub['romaji']}\n"
            )
            en_entries.append(
                f"{idx}\n{start_str} --> {end_str}\n{sub['en']}\n"
            )

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(
            zip_buffer, "w", zipfile.ZIP_DEFLATED
        ) as zip_archive:
            zip_archive.writestr(f"{base_name}_JP.srt", "\n".join(jp_entries))
            zip_archive.writestr(
                f"{base_name}_Romaji.srt", "\n".join(romaji_entries)
            )
            zip_archive.writestr(f"{base_name}_EN.srt", "\n".join(en_entries))

        zip_buffer.seek(0)

        return send_file(
            zip_buffer,
            as_attachment=True,
            download_name=f"{base_name}_subtitles.zip",
            mimetype="application/zip",
        )

    finally:
        if os.path.exists(temp_audio_path):
            os.remove(temp_audio_path)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
