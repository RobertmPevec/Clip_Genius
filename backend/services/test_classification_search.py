from openai import OpenAI
import os
import tiktoken
import math
import numpy as np
import ffmpeg
import nemo.collections.asr as nemo_asr
from omegaconf import open_dict
from sentence_transformers import SentenceTransformer
import faiss
import concurrent.futures
import subprocess

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

# Get vector embedding from OpenAI
def get_embedding(text, model="text-embedding-3-large"):
    text = text.replace("\n", " ")
    response = openai.embeddings.create(input=[text], model=model)
    embedding = response.data[0].embedding
    return embedding

# Load NeMo ASR model
asr_model = nemo_asr.models.ASRModel.from_pretrained("stt_en_fastconformer_transducer_large")
index = faiss.read_index("highlight_vectors.faiss")
minilm_model = SentenceTransformer("all-MiniLM-L6-v2")

# Ensure freeze() is applied before unfreeze()
asr_model.freeze()

def create_clip(input_file, start_time, end_time, output_folder, output_filename):
    """
    Extracts a video segment using FFmpeg.

    Parameters:
        input_file - The input video file path (str)
        start_time - The start time of the segment (str, HH:MM:SS format)
        end_time - The end time of the segment (str, HH:MM:SS format)
        output_folder - The folder where the output file will be saved (str)
        output_filename - The name of the output video file (str)
    """
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    output_path = os.path.join(output_folder, output_filename)

    command = [
        "ffmpeg",
        "-i", input_file,
        "-ss", start_time,
        "-to", end_time,
        "-c", "copy",
        output_path
    ]
    
    subprocess.run(command, check=True)
    print(f"✅ Created clip: {output_path}")

def format_time(seconds):
    """ Converts seconds into HH:MM:SS format """
    return f"{seconds//3600:02}:{(seconds%3600)//60:02}:{seconds%60:02}"

# Helper function for transcribe_embed_filter_nemo
def process_file(file, fallback_index, folder_path, chunk_duration, alpha):
    file_path = os.path.join(folder_path, file)

    while True: # Set infinite loop until works
        try:
            hypotheses = asr_model.transcribe([file_path], return_hypotheses=True)
            transcript = hypotheses[0].text if isinstance(hypotheses, list) else hypotheses.text
            embedding = minilm_model.encode(transcript)
            distances, _ = index.search(np.array([embedding]), k=8)
            try:
                index_val = int(file.split("_")[1].split(".")[0])
            except Exception:
                index_val = fallback_index

            time_stamp = index_val * chunk_duration
            similarity_score = 1 - np.mean(distances[0]) if len(distances[0]) > 0 else 0
            time_bonus = alpha * time_stamp
            adjusted_score = similarity_score + time_bonus
            os.remove(file_path)
            return (time_stamp, adjusted_score)

        except Exception as e:
            print(f"Error processing {file}: {e}")
            print("Retrying...")

# Transcibe, embed, rank and filter transcripts but in parallel
def transcribe_embed_filter_nemo(folder_path="clips/", chunk_duration=30, keep_ratio=0.4, alpha=0.00003):
    # Gather all .wav files
    clip_files = [f for f in os.listdir(folder_path) if f.endswith(".wav")]
    results = []
    max_workers = min(8, len(clip_files))

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(process_file, file, idx, folder_path, chunk_duration, alpha): file
            for idx, file in enumerate(clip_files)
        }

        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                print(f"Error processing file {futures[future]}: {e}")

    results.sort(key=lambda x: x[1], reverse=True)
    keep_count = max(1, int(len(results) * keep_ratio))
    filtered_dict = {ts: score for ts, score in results[:keep_count]}
    
    return filtered_dict

def process_clip(i, n, input_file, output_folder):
    start_time = format_time(n)
    end_time = format_time(n + 35)
    output_filename = f"clip_{i + 1}.mp4"
    create_clip(input_file, start_time, end_time, output_folder, output_filename)

def merge_clips(output_folder, final_output="merged_video.mp4", delete_clips=True):
    """ Merges all mp4 clips into one final MP4 file using FFmpeg and deletes the clips after merging. """
    file_list_path = os.path.join(output_folder, "file_list.txt")
    
    clips = sorted([f for f in os.listdir(output_folder) if f.endswith(".mp4")], key=lambda x: int(x.split("_")[1].split(".")[0]))

    with open(file_list_path, "w") as f:
        for clip in clips:
            f.write(f"file '{os.path.join(output_folder, clip)}'\n")

    final_output_path = os.path.join(output_folder, final_output)
    command = ["ffmpeg", "-f", "concat", "-safe", "0", "-i", file_list_path, "-c", "copy", final_output_path]
    subprocess.run(command, check=True)

    if delete_clips:
        for clip in clips:
            os.remove(os.path.join(output_folder, clip))

def output_video(dictionary, input_file):
    times = sorted(dictionary.keys())
    output_folder = os.path.join(os.path.dirname(__file__), "clips")

    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(process_clip, i, n, input_file, output_folder): n for i, n in enumerate(times)}

        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"⚠️ Error processing clip: {e}")

    merge_clips(output_folder, delete_clips=True)

    if os.path.exists(input_file):
        os.remove(input_file)