# iPhone Voice → WAV (Streamlit)

A simple, private-by-default voice-over utility. Upload one `.m4a` recording from iPhone Voice Memos, preview it, and download the processed WAV.

## Processing contract

- Automatic first/last speech detection using WebRTC VAD; optional manual boundaries for difficult recordings.
- Preserve the natural voice; no denoising, reverb, or artistic effects.
- **Two-pass FFmpeg EBU R128 loudness normalization:** target **−16 LUFS** with a **−1.5 dBTP** true peak ceiling and 11 LU loudness range target.
- Output: **48,000 Hz, 1 channel, 16-bit PCM WAV**.
- **Exactly 48,000 zero-valued samples (1.000 s) prepended and appended** to the normalized selection.
- One file at a time. Maximum input: 100 MB, 15 minutes. Output is previewable and downloadable.
- No paid APIs, tokens, external audio-processing services, or saved recordings. Temporary data is deleted after each conversion.

**Speech boundary note:** Automatic voice-activity detection estimates the first and last spoken sound; it may miss very soft consonants or mistake loud background noise for speech. A small safety margin is retained. Review the WAV preview, and choose **Advanced: choose speech start/end manually** if needed. The digital padding is always exactly 1.000 s on each side of the *selected audio*, even if that selection retains a small amount of room tone.

## Run locally

Install Python 3.11–3.13 and FFmpeg (which includes `ffprobe`), then:

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

Open the local URL shown by Streamlit.

## Deploy to Streamlit Community Cloud (from a private GitHub repo)

1. Create a new **private** GitHub repository named `iphone-voice-wav-converter` and upload the files from this project, retaining `.streamlit/config.toml`.
2. At https://share.streamlit.io/ sign in with GitHub. Give Streamlit permission to access **private** repositories (Workspace settings → Linked accounts if needed).
3. Select **Create app** → **Yup, I have an app**.
4. Select the `iphone-voice-wav-converter` repository, `main` branch, and `app.py` as the entrypoint; deploy.
5. Once deployed, open the private Streamlit app and test a short M4A voice memo. Make sure the Streamlit workspace visibility is configured as intended.

`requirements.txt` installs the Python packages, and `packages.txt` installs the system FFmpeg package automatically on Community Cloud. Community Cloud may limit how many private apps you can run concurrently. Deploy and privacy permissions are managed in your Streamlit account.

## Tests

```bash
python -m pip install pytest
pytest -q
```

Integration tests require FFmpeg and `espeak` (a synthetic voice generator; not required by the app).

## Privacy and limitations

On Cloud, audio is uploaded to the Streamlit server and processed there; this is **not** offline/local processing. Processing uses short-lived temporary files, and the app itself does not store recordings in GitHub, databases, or object storage. Streamlit's hosting infrastructure and browser session caching have their own policies. Don't upload confidential recordings unless you're comfortable with that hosting.
