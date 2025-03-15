from openai import OpenAI
import os
import tiktoken
import math
import numpy as np
import ffmpeg
import nemo.collections.asr as nemo_asr
from omegaconf import open_dict
from sentence_transformers import SentenceTransformer

# Put in API key to use Open AI
client = OpenAI(api_key="")

# Find the amount of tokens in a string
def num_of_tokens_from_string(text: str, encoding_name: str = "cl100k_base") -> int:
    encoding = tiktoken.get_encoding(encoding_name)
    tokens = encoding.encode(text)
    return len(tokens)

# Check the simularity of two vectors
def cosine_simularity(vector1, vector2):
    vector1 = np.array(vector1)
    vector2 = np.array(vector2)
    dot_product = np.dot(vector1, vector2)
    norm1 = np.linalg.norm(vector1)
    norm2 = np.linalg.norm(vector2)
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot_product / (norm1 * norm2)

# Extract an .wav of the audio from user's mp4 file
def extract_audio(video_path, audio_output="temp_audio.wav"):
    ffmpeg.input(video_path).output(audio_output, format="wav", acodec="pcm_s16le", ar=16000, ac=1).run(overwrite_output=True)
    return audio_output

# Split audio into 30 second segments + 5 second buffer
def split_audio(input_audio, output_folder="clips", chunk_length=30, buffer=5, delete_original=True):
    try:
        os.makedirs(output_folder, exist_ok=True)

        # Get audio duration
        probe = ffmpeg.probe(input_audio)
        duration = float(probe['format']['duration'])

        num_chunks = math.ceil(duration / chunk_length)
        output_files = []

        for i in range(num_chunks):
            start_time = i * chunk_length
            actual_chunk_length = min(chunk_length + buffer, duration - start_time)

            output_file = os.path.join(output_folder, f"chunk_{i}.wav")
            output_files.append(output_file)

            ffmpeg.input(input_audio, ss=start_time, t=actual_chunk_length).output(
                output_file, format="wav", acodec="pcm_s16le", ar=16000, ac=1
            ).run(overwrite_output=True)

        if delete_original:
            os.remove(input_audio)
            print(f"Deleted original file: {input_audio}")

        return output_files

    except Exception as e:
        print("Error:", e)
        return []

# Transcribe audio to get transcript
def transcribe_audio(audio_path):
    with open(audio_path, "rb") as file:
        transcript = openai.audio.transcriptions.create(
            model="whisper-1",
            file=file
        )
    return transcript.text

# We load the model initialy so we dont have to load it every call
minilm_model = SentenceTransformer("all-MiniLM-L6-v2")

# Get vector embedding locally
def MiniLM_embedding(text):
    """Returns the embedding vector for the given text using MiniLM."""
    return minilm_model.encode(text)

# Transcribe audio locally using NeMo and insert into dictionary
def transcribe_and_embed_nemo(folder_path="clips/", chunk_duration=30):
    asr_model = nemo_asr.models.ASRModel.from_pretrained("stt_en_fastconformer_transducer_large")
    clip_files = [f for f in os.listdir(folder_path) if f.endswith(".wav")]
    results = {}
    
    # Process each file
    for file in clip_files:
        file_path = os.path.join(folder_path, file)
        
        hypotheses = asr_model.transcribe([file_path], return_hypotheses=True)
        if isinstance(hypotheses, tuple):
            hypotheses = hypotheses[0]
        transcript = hypotheses[0].text
        
        # Get embedding for the transcript
        embedding = MiniLM_embedding(transcript)
        
        try:
            index = int(file.split("_")[1].split(".")[0])
        except Exception:
            index = len(results)
        time_stamp = index * chunk_duration
        
        results[time_stamp] = embedding.tolist()
        
        # Delete no longer needed file
        os.remove(file_path)
        print(f"Deleted {file}")
        
    return results

# Get vector embedding 
def get_embedding(text, model="text-embedding-3-large"):
    text = text.replace("\n", " ")
    response = openai.embeddings.create(input=[text], model=model)
    embedding = response.data[0].embedding
    return embedding
