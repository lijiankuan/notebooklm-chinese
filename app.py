"""
main.py
"""

# Standard library imports
import glob
import os
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import List, Tuple, Optional

# Third-party imports
import gradio as gr
import random
from loguru import logger
from pypdf import PdfReader
from pydub import AudioSegment

# Local imports
from constants import (
    APP_TITLE,
    CHARACTER_LIMIT,
    ERROR_MESSAGE_NOT_PDF,
    ERROR_MESSAGE_NO_INPUT,
    ERROR_MESSAGE_NOT_SUPPORTED_IN_MELO_TTS,
    ERROR_MESSAGE_READING_PDF,
    ERROR_MESSAGE_TOO_LONG,
    GRADIO_CACHE_DIR,
    GRADIO_CLEAR_CACHE_OLDER_THAN,
    MELO_TTS_LANGUAGE_MAPPING,
    NOT_SUPPORTED_IN_MELO_TTS,
    SUNO_LANGUAGE_MAPPING,
    UI_ALLOW_FLAGGING,
    UI_API_NAME,
    UI_CACHE_EXAMPLES,
    UI_CONCURRENCY_LIMIT,
    UI_DESCRIPTION,
    UI_EXAMPLES,
    UI_INPUTS,
    UI_OUTPUTS,
    UI_SHOW_API,
    INTRO_MUSIC_PATH,
    INTRO_MUSIC_DURATION,
    MUSIC_VOLUME_REDUCTION,
    FADE_OUT_DURATION,
)
from prompts import (
    LANGUAGE_MODIFIER,
    LENGTH_MODIFIERS,
    QUESTION_MODIFIER,
    SYSTEM_PROMPT,
    TONE_MODIFIER,
)
from schema import ShortDialogue, MediumDialogue
from utils import generate_podcast_audio, generate_script, parse_url


def generate_podcast(
    files: List[str],
    url: Optional[str],
    question: Optional[str],
    tone: Optional[str],
    length: Optional[str],
    language: str,
    use_advanced_audio: bool,
) -> Tuple[str, str]:
    """Generate the audio and transcript from the PDFs and/or URL."""

    text = ""

    # Choose random number from 0 to 8
    random_voice_number = random.randint(0, 8) # this is for suno model

    if not use_advanced_audio and language in NOT_SUPPORTED_IN_MELO_TTS:
        raise gr.Error(ERROR_MESSAGE_NOT_SUPPORTED_IN_MELO_TTS)

    # Check if at least one input is provided
    if not files and not url:
        raise gr.Error(ERROR_MESSAGE_NO_INPUT)

    # Process PDFs if any
    if files:
        for file in files:
            if not file.lower().endswith(".pdf"):
                raise gr.Error(ERROR_MESSAGE_NOT_PDF)

            try:
                with Path(file).open("rb") as f:
                    reader = PdfReader(f)
                    text += "\n\n".join([page.extract_text() for page in reader.pages])
            except Exception as e:
                raise gr.Error(f"{ERROR_MESSAGE_READING_PDF}: {str(e)}")

    # Process URL if provided
    if url:
        try:
            url_text = parse_url(url)
            text += "\n\n" + url_text
        except ValueError as e:
            raise gr.Error(str(e))

    # Check total character count
    if len(text) > CHARACTER_LIMIT:
        raise gr.Error(ERROR_MESSAGE_TOO_LONG)

    # Modify the system prompt based on the user input
    modified_system_prompt = SYSTEM_PROMPT

    if question:
        modified_system_prompt += f"\n\n{QUESTION_MODIFIER} {question}"
    if tone:
        modified_system_prompt += f"\n\n{TONE_MODIFIER} {tone}."
    if length:
        modified_system_prompt += f"\n\n{LENGTH_MODIFIERS[length]}"
    if language:
        modified_system_prompt += f"\n\n{LANGUAGE_MODIFIER} {language}."

    # Call the LLM
    if length == "Short (1-2 min)":
        llm_output = generate_script(modified_system_prompt, text, ShortDialogue)
    else:
        llm_output = generate_script(modified_system_prompt, text, MediumDialogue)

    logger.info(f"Generated dialogue: {llm_output}")

    # Process the dialogue
    audio_segments = []
    transcript = ""
    total_characters = 0

    # 添加开场音乐
    intro_music = AudioSegment.from_file(INTRO_MUSIC_PATH)[:INTRO_MUSIC_DURATION]  # 使用配置的时长
    intro_music = intro_music - MUSIC_VOLUME_REDUCTION  # 降低音量
    intro_music = intro_music.fade_out(duration=FADE_OUT_DURATION)  # 淡出效果
    
    # 创建最终的音频列表，以开场音乐开始
    final_audio_segments = [intro_music]
    
    # 处理对话片段
    for line in llm_output.dialogue:
        logger.info(f"Generating audio for {line.speaker}: {line.text}")
        if line.speaker == "Host (Jane)":
            speaker = f"**Host**: {line.text}"
        else:
            speaker = f"**{llm_output.name_of_guest}**: {line.text}"
        transcript += speaker + "\n\n"
        total_characters += len(line.text)

        language_for_tts = SUNO_LANGUAGE_MAPPING[language]

        if not use_advanced_audio:
            language_for_tts = MELO_TTS_LANGUAGE_MAPPING[language_for_tts]

        # Get audio file path
        audio_file_path = generate_podcast_audio(
            line.text, line.speaker, language_for_tts, use_advanced_audio, random_voice_number
        )
        # Read the audio file into an AudioSegment
        audio_segment = AudioSegment.from_file(audio_file_path)
        final_audio_segments.append(audio_segment)

    # 合并所有音频片段
    combined_audio = sum(final_audio_segments)

    # Export the combined audio to a temporary file
    temporary_directory = GRADIO_CACHE_DIR
    os.makedirs(temporary_directory, exist_ok=True)

    temporary_file = NamedTemporaryFile(
        dir=temporary_directory,
        delete=False,
        suffix=".mp3",
    )
    combined_audio.export(temporary_file.name, format="mp3")

    # Delete any files in the temp directory that end with .mp3 and are over a day old
    for file in glob.glob(f"{temporary_directory}*.mp3"):
        if (
            os.path.isfile(file)
            and time.time() - os.path.getmtime(file) > GRADIO_CLEAR_CACHE_OLDER_THAN
        ):
            os.remove(file)

    logger.info(f"Generated {total_characters} characters of audio")

    return temporary_file.name, transcript


demo = gr.Interface(
    title=APP_TITLE,
    description=UI_DESCRIPTION,
    fn=generate_podcast,
    inputs=[
        gr.File(
            label=UI_INPUTS["file_upload"]["label"],  # Step 1: File upload
            file_types=UI_INPUTS["file_upload"]["file_types"],
            file_count=UI_INPUTS["file_upload"]["file_count"],
        ),
        gr.Textbox(
            label=UI_INPUTS["url"]["label"],  # Step 2: URL
            placeholder=UI_INPUTS["url"]["placeholder"],
        ),
        gr.Textbox(label=UI_INPUTS["question"]["label"]),  # Step 3: Question
        gr.Dropdown(
            label=UI_INPUTS["tone"]["label"],  # Step 4: Tone
            choices=UI_INPUTS["tone"]["choices"],
            value=UI_INPUTS["tone"]["value"],
        ),
        gr.Dropdown(
            label=UI_INPUTS["length"]["label"],  # Step 5: Length
            choices=UI_INPUTS["length"]["choices"],
            value=UI_INPUTS["length"]["value"],
        ),
        gr.Dropdown(
            choices=UI_INPUTS["language"]["choices"],  # Step 6: Language
            value=UI_INPUTS["language"]["value"],
            label=UI_INPUTS["language"]["label"],
        ),
        gr.Checkbox(
            label=UI_INPUTS["advanced_audio"]["label"],
            value=UI_INPUTS["advanced_audio"]["value"],
        ),
    ],
    outputs=[
        gr.Audio(
            label=UI_OUTPUTS["audio"]["label"], format=UI_OUTPUTS["audio"]["format"]
        ),
        gr.Markdown(label=UI_OUTPUTS["transcript"]["label"]),
    ],
    allow_flagging=UI_ALLOW_FLAGGING,
    api_name=UI_API_NAME,
    theme=gr.themes.Ocean(),
    concurrency_limit=UI_CONCURRENCY_LIMIT,
    examples=UI_EXAMPLES,
    cache_examples=UI_CACHE_EXAMPLES,
)

if __name__ == "__main__":
    demo.launch(show_api=UI_SHOW_API)