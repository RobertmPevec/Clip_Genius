"""-------------------------------------------------------
CLIP GENIUS: MultiProcess
-------------------------------------------------------
Author:  JD
ID:      91786
Uses:    threading, queue
Version:  1.0.8
__updated__ = Fri Mar 14 2025
-------------------------------------------------------
"""

from ImageProcessingFunctions import analyze_segment
from PreProcessing import get_video_duration
import threading
import queue
import os


def thread_wrapper(segment_path, score_coords, duration, result_queue):
    """
    -------------------------------------------------------
    Analyzes video segments in parallel using multiple threads to detect score changes.
    Use: results = analyze_segments_with_threads(segment_folder, score_coords)
    -------------------------------------------------------
    Parameters:
        segment_folder - the directory containing the video segments to analyze (str)
        score_coords - coordinates (x, y, w, h)
    Returns:
        results - a list of results from analyzing the segments (list)
    -------------------------------------------------------
    """
    result = analyze_segment(segment_path, score_coords, duration)
    result_queue.put(result)


def analayze_segments_with_threads(segment_folder, score_coords):
    """
    -------------------------------------------------------
    Analyzes video segments in parallel using multiple threads to detect score changes.
    Use: results = analyze_segments_with_threads(segment_folder, score_coords)
    -------------------------------------------------------
    Parameters:
        segment_folder - the directory containing the video segments to analyze (str)
        score_coords - coordinates (x, y, w, h)
    Returns:
        results - a list of results
    -------------------------------------------------------
    """
    result_queue = queue.Queue()
    threads = []
    duration = 0

    segments = os.listdir(os.path.join(segment_folder))
    for i, segment in enumerate(segments):
        segment = os.path.join(segment_folder, segment)
        print(f"{i} - {segment} @ {duration}")
        thread = threading.Thread(
            target=thread_wrapper,
            args=(segment, score_coords, duration, result_queue),
        )
        duration += round(get_video_duration(segment), 2)

        thread.start()
        threads.append(thread)

    for thread in threads:
        thread.join()

    results = []
    while not result_queue.empty():
        results.extend(result_queue.get())

    return results
