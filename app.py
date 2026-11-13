"""One-click iPhone voice M4A to broadcast-friendly mono WAV, for Streamlit."""
from __future__ import annotations

import streamlit as st

from audio_processing import AudioProcessingError, MAX_UPLOAD_BYTES, convert_m4a_to_wav

st.set_page_config(
    page_title="iPhone Voice → WAV",
    page_icon="🎙️",
    layout="centered",
)

st.title("🎙️ iPhone Voice → WAV")
st.write("Convert an iPhone Voice Memos recording into a cleanly formatted voice-over WAV file.")
st.caption("-16 LUFS · 48 kHz · mono · 16-bit PCM · exactly 1 second of digital silence at each end")

upload = st.file_uploader("Choose your iPhone .m4a recording", type=["m4a"], accept_multiple_files=False)

if upload is not None:
    content = upload.getvalue()
    if len(content) > MAX_UPLOAD_BYTES:
        st.error("Recording exceeds 100 MB. Please use a shorter recording.")
        st.stop()
    st.subheader("1. Original recording")
    st.audio(content, format="audio/mp4")
    st.caption(f"{upload.name} · {len(content) / (1024*1024):.1f} MB")

    with st.form("convert_form"):
        use_manual = st.checkbox("Advanced: choose speech start/end manually", value=False,
                                 help="Use this only if automatic voice detection misses a word or includes unwanted audio.")
        start = end = None
        if use_manual:
            st.caption("Enter the beginning of your first word and the end of your last word (seconds).")
            start = st.number_input("First word starts at (seconds)", min_value=0.0, value=0.0, step=0.1, format="%.2f")
            end = st.number_input("Last word ends at (seconds)", min_value=0.0, value=15.0, step=0.1, format="%.2f")
        convert = st.form_submit_button("Convert to WAV", type="primary", use_container_width=True)

    if convert:
        with st.spinner("Detecting speech and balancing voice volume…"):
            try:
                result = convert_m4a_to_wav(
                    content, manual_start=start if use_manual else None,
                    manual_end=end if use_manual else None,
                )
            except AudioProcessingError as exc:
                st.error(str(exc))
            except Exception:
                st.error("Unexpected conversion issue. Please check your recording and try again.")
            else:
                st.session_state["result"] = result
                st.session_state["source_identity"] = (upload.name, len(content), hash(content))

    result = st.session_state.get("result")
    identity = st.session_state.get("source_identity")
    if result is not None and identity == (upload.name, len(content), hash(content)):
        st.success("WAV ready — your recording has been converted.")
        st.subheader("2. Preview your WAV")
        st.audio(result.wav_bytes, format="audio/wav")
        st.caption(
            f"Speech selection: {result.speech_window.start_seconds:.2f}–"
            f"{result.speech_window.end_seconds:.2f}s "
            f"({result.speech_window.detection}) · "
            f"Output: {result.output_duration_seconds:.2f}s"
        )
        filename = upload.name.rsplit(".", 1)[0] + "_48k_mono_normalized.wav"
        st.download_button(
            "⬇️ Download WAV", result.wav_bytes, file_name=filename,
            mime="audio/wav", use_container_width=True, type="primary",
        )
else:
    st.info("Upload a single .m4a recording to begin. No account or paid API key is required.")

st.divider()
st.caption("Original voice character is preserved: no denoising, reverb, or compression effects are added. "
           "The app uses temporary processing files deleted after each conversion. "
           "On Streamlit Cloud, recordings are transmitted to the app server for processing.")
