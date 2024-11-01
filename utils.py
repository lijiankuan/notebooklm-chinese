"""
utils.py

Functions:
- generate_script: Get the dialogue from the LLM.
- call_llm: Call the LLM with the given prompt and dialogue format.
- parse_url: Parse the given URL and return the text content.
- generate_podcast_audio: Generate audio for podcast using Azure TTS or advanced audio models.
- _use_suno_model: Generate advanced audio using Bark.
- _use_azure_tts: Generate audio using Azure Speech API.
- _get_melo_tts_params: Get TTS parameters based on speaker and language.
"""

# Standard library imports
import time
from typing import Any, Union
from tempfile import NamedTemporaryFile
import json
import os
import logging

# Third-party imports
import instructor
import requests
from bark import SAMPLE_RATE, generate_audio, preload_models
from fireworks.client import Fireworks
from gradio_client import Client
from scipy.io.wavfile import write as write_wav
import azure.cognitiveservices.speech as speechsdk

# Local imports
from constants import (
    DEFAULT_AZURE_VOICES,
    FIREWORKS_API_KEY,
    FIREWORKS_MODEL_ID,
    FIREWORKS_MAX_TOKENS,
    FIREWORKS_TEMPERATURE,
    MELO_API_NAME,
    MELO_TTS_SPACES_ID,
    MELO_RETRY_ATTEMPTS,
    MELO_RETRY_DELAY,
    JINA_READER_URL,
    JINA_RETRY_ATTEMPTS,
    JINA_RETRY_DELAY,
    AZURE_SPEECH_KEY,
    AZURE_SPEECH_REGION,
    AZURE_SPEECH_VOICES,
)
from schema import ShortDialogue, MediumDialogue

# Initialize Fireworks client, with Instructor patch
fw_client = Fireworks(api_key=FIREWORKS_API_KEY)
fw_client = instructor.from_fireworks(fw_client)

# Initialize Hugging Face client
hf_client = Client(MELO_TTS_SPACES_ID)

# Download and load all models for Bark
preload_models()

# 在其他初始化代码之前添加
logger = logging.getLogger(__name__)

def generate_script(
    system_prompt: str,
    input_text: str,
    output_model: Union[ShortDialogue, MediumDialogue],
) -> Union[ShortDialogue, MediumDialogue]:
    """Get the dialogue from the LLM."""

    # Call the LLM for the first time
    first_draft_dialogue = call_llm(system_prompt, input_text, output_model)

    # Call the LLM a second time to improve the dialogue
    system_prompt_with_dialogue = f"{system_prompt}\n\nHere is the first draft of the dialogue you provided:\n\n{first_draft_dialogue.model_dump_json()}."
    final_dialogue = call_llm(system_prompt_with_dialogue, "Please improve the dialogue. Make it more natural and engaging.", output_model)

    return final_dialogue


def call_llm(system_prompt: str, text: str, dialogue_format: Any) -> Any:
    """Call the LLM with the given prompt and dialogue format."""
    response = fw_client.chat.completions.create(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        model=FIREWORKS_MODEL_ID,
        max_tokens=FIREWORKS_MAX_TOKENS,
        temperature=FIREWORKS_TEMPERATURE,
        response_model=dialogue_format,
    )
    return response


def parse_url(url: str) -> str:
    """Parse the given URL and return the text content."""
    for attempt in range(JINA_RETRY_ATTEMPTS):
        try:
            full_url = f"{JINA_READER_URL}{url}"
            response = requests.get(full_url, timeout=60)
            response.raise_for_status()  # Raise an exception for bad status codes
            break
        except requests.RequestException as e:
            if attempt == JINA_RETRY_ATTEMPTS - 1:  # Last attempt
                raise ValueError(
                    f"Failed to fetch URL after {JINA_RETRY_ATTEMPTS} attempts: {e}"
                ) from e
            time.sleep(JINA_RETRY_DELAY)  # Wait for X second before retrying
    return response.text


def generate_podcast_audio(
    text: str, speaker: str, language: str, use_advanced_audio: bool, random_voice_number: int
) -> str:
    """Generate audio for podcast using Melo TTS or advanced Azure Speech API."""
    if use_advanced_audio:
        return _use_azure_tts(text, speaker, language)
    else:
        return _use_melotts_api(text, speaker, language)


def _use_suno_model(text: str, speaker: str, language: str, random_voice_number: int) -> str:
    """Generate advanced audio using Bark."""
    host_voice_num = str(random_voice_number)
    guest_voice_num = str(random_voice_number + 1)
    audio_array = generate_audio(
        text,
        history_prompt=f"v2/{language}_speaker_{host_voice_num if speaker == 'Host (Jane)' else guest_voice_num}",
    )
    file_path = f"audio_{language}_{speaker}.mp3"
    write_wav(file_path, SAMPLE_RATE, audio_array)
    return file_path


def _use_melotts_api(text: str, speaker: str, language: str) -> str:
    """Generate audio using TTS model."""
    accent, speed = _get_melo_tts_params(speaker, language)

    for attempt in range(MELO_RETRY_ATTEMPTS):
        try:
            return hf_client.predict(
                text=text,
                language=language,
                speaker=accent,
                speed=speed,
                api_name=MELO_API_NAME,
            )
        except Exception as e:
            if attempt == MELO_RETRY_ATTEMPTS - 1:  # Last attempt
                raise  # Re-raise the last exception if all attempts fail
            time.sleep(MELO_RETRY_DELAY)  # Wait for X second before retrying


def _use_azure_tts(text: str, speaker: str, language: str) -> str:
    """Generate audio using Azure Speech API."""
    # 使用环境变量或默认值
    azure_voices = json.loads(os.getenv('AZURE_SPEECH_VOICES', 'null')) or DEFAULT_AZURE_VOICES
    try:
        voice_name = azure_voices[language][speaker]
    except (KeyError, TypeError):
        logger.warning(f"Voice not found for {language}/{speaker}, using default")
        voice_name = DEFAULT_AZURE_VOICES[language][speaker]
    
    # 配置 speech config
    speech_config = speechsdk.SpeechConfig(
        subscription=AZURE_SPEECH_KEY, 
        region=AZURE_SPEECH_REGION
    )
    
    # 获取对应的voice
    speech_config.speech_synthesis_voice_name = voice_name

    # 创建临时文件
    temp_file = NamedTemporaryFile(suffix=".mp3", delete=False)
    audio_config = speechsdk.audio.AudioOutputConfig(filename=temp_file.name)
    
    # 创建synthesizer
    speech_synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config, 
        audio_config=audio_config
    )

    # 生成语音
    result = speech_synthesizer.speak_text_async(text).get()
    
    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        return temp_file.name
    else:
        raise Exception(f"Speech synthesis failed: {result.reason}")


def _get_melo_tts_params(speaker: str, language: str) -> tuple[str, float]:
    """Get TTS parameters based on speaker and language."""
    if speaker == "Guest":
        accent = "EN-US" if language == "EN" else language
        speed = 0.9
    else:  # host
        accent = "EN-Default" if language == "EN" else language
        speed = (
            1.1 if language != "EN" else 1
        )  # if the language is not English, try speeding up so it'll sound different from the host
        # for non-English, there is only one voice
    return accent, speed
