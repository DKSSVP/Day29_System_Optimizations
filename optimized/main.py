
import cv2
import pyaudio
import wave
import queue
import threading
import time
from pathlib import Path
from collections import deque


# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

TEST_DURATION = 60

CAMERA_INDEX = 0
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
TARGET_FPS = 30

AUDIO_RATE = 44100
AUDIO_CHANNELS = 1
AUDIO_CHUNK = 512
AUDIO_FORMAT = pyaudio.paInt16

VIDEO_QUEUE_SIZE = 8
AUDIO_QUEUE_SIZE = 32

BASE_DIR = Path(__file__).resolve().parent

VIDEO_OUTPUT = BASE_DIR / "optimized_video.avi"
AUDIO_OUTPUT = BASE_DIR / "optimized_audio.wav"


# --------------------------------------------------
# SHARED OBJECTS
# --------------------------------------------------

video_queue = queue.Queue(maxsize=VIDEO_QUEUE_SIZE)
audio_queue = queue.Queue(maxsize=AUDIO_QUEUE_SIZE)

stop_event = threading.Event()

metrics_lock = threading.Lock()
display_lock = threading.Lock()

latest_frame = None

metrics = {
    "video_captured": 0,
    "video_processed": 0,
    "video_dropped": 0,
    "video_latency": [],
    "video_processing_time": [],
    "video_queue_peak": 0,

    "audio_captured": 0,
    "audio_processed": 0,
    "audio_dropped": 0,
    "audio_latency": [],
    "audio_processing_time": [],
    "audio_queue_peak": 0,
}


# --------------------------------------------------
# VIDEO CAPTURE THREAD
# --------------------------------------------------

def video_capture_worker(camera):
    global latest_frame

    while not stop_event.is_set():
        capture_start = time.perf_counter()

        success, frame = camera.read()

        if not success:
            if not stop_event.is_set():
                print("Warning: Unable to capture video frame.")
                stop_event.set()
            break

        capture_timestamp = time.perf_counter()

        with metrics_lock:
            metrics["video_captured"] += 1

        with display_lock:
            latest_frame = frame.copy()

        item = (frame, capture_timestamp)

        try:
            video_queue.put_nowait(item)

        except queue.Full:
            # Drop the oldest frame to prioritize fresh frames.
            try:
                video_queue.get_nowait()

                with metrics_lock:
                    metrics["video_dropped"] += 1

            except queue.Empty:
                pass

            try:
                video_queue.put_nowait(item)
            except queue.Full:
                with metrics_lock:
                    metrics["video_dropped"] += 1

        with metrics_lock:
            metrics["video_queue_peak"] = max(
                metrics["video_queue_peak"],
                video_queue.qsize()
            )


# --------------------------------------------------
# VIDEO PROCESSING THREAD
# --------------------------------------------------

def video_processing_worker(video_writer):
    while True:
        item = video_queue.get()

        # Sentinel indicates that all captured frames
        # have been queued and the worker can exit.
        if item is None:
            break

        frame, capture_timestamp = item

        processing_start = time.perf_counter()

        video_writer.write(frame)

        processing_end = time.perf_counter()

        latency_ms = (
            processing_start - capture_timestamp
        ) * 1000

        processing_ms = (
            processing_end - processing_start
        ) * 1000

        with metrics_lock:
            metrics["video_processed"] += 1
            metrics["video_latency"].append(latency_ms)
            metrics["video_processing_time"].append(
                processing_ms
            )


# --------------------------------------------------
# AUDIO CAPTURE THREAD
# --------------------------------------------------

def audio_capture_worker(stream):
    while not stop_event.is_set():
        try:
            audio_data = stream.read(
                AUDIO_CHUNK,
                exception_on_overflow=False
            )

        except Exception as error:
            if not stop_event.is_set():
                print("Audio capture error:", error)
                stop_event.set()
            break

        capture_timestamp = time.perf_counter()

        with metrics_lock:
            metrics["audio_captured"] += 1

        item = (audio_data, capture_timestamp)

        try:
            audio_queue.put(item, timeout=0.1)

        except queue.Full:
            with metrics_lock:
                metrics["audio_dropped"] += 1

        with metrics_lock:
            metrics["audio_queue_peak"] = max(
                metrics["audio_queue_peak"],
                audio_queue.qsize()
            )


# --------------------------------------------------
# AUDIO PROCESSING THREAD
# --------------------------------------------------

def audio_processing_worker(audio_writer):
    while True:
        item = audio_queue.get()

        # Sentinel indicates that all captured audio
        # chunks have been queued.
        if item is None:
            break

        audio_data, capture_timestamp = item

        processing_start = time.perf_counter()

        audio_writer.writeframesraw(audio_data)

        processing_end = time.perf_counter()

        latency_ms = (
            processing_start - capture_timestamp
        ) * 1000

        processing_ms = (
            processing_end - processing_start
        ) * 1000

        with metrics_lock:
            metrics["audio_processed"] += 1
            metrics["audio_latency"].append(latency_ms)
            metrics["audio_processing_time"].append(
                processing_ms
            )


# --------------------------------------------------
# METRIC HELPERS
# --------------------------------------------------

def average(values):
    if not values:
        return 0.0

    return sum(values) / len(values)


def save_metrics(
    actual_duration,
    process_cpu_start,
    process_cpu_end,
    video_thread,
    audio_thread,
    video_processor,
    audio_processor
):
    cpu_time = process_cpu_end - process_cpu_start

    cpu_utilization = (
        cpu_time / actual_duration
    ) * 100 if actual_duration > 0 else 0

    audio_duration = (
        metrics["audio_processed"] * AUDIO_CHUNK
    ) / AUDIO_RATE

    video_fps = (
        metrics["video_processed"] / actual_duration
        if actual_duration > 0 else 0
    )

    video_average_latency = average(
        metrics["video_latency"]
    )

    audio_average_latency = average(
        metrics["audio_latency"]
    )

    video_processing_average = average(
        metrics["video_processing_time"]
    )

    audio_processing_average = average(
        metrics["audio_processing_time"]
    )

    print("\n" + "=" * 55)
    print("DAY 29 - OPTIMIZED SYSTEM PERFORMANCE")
    print("=" * 55)

    print(f"\nTest duration: {actual_duration:.2f} seconds")

    print("\nVIDEO METRICS")
    print("-" * 40)
    print(f"Captured frames: {metrics['video_captured']}")
    print(f"Processed frames: {metrics['video_processed']}")
    print(f"Dropped frames: {metrics['video_dropped']}")
    print(f"Average processing FPS: {video_fps:.2f}")
    print(
        "Video queue high-water mark: "
        f"{metrics['video_queue_peak']}"
    )
    print(
        "Average capture-to-processing latency: "
        f"{video_average_latency:.2f} ms"
    )
    print(
        "Average video processing time: "
        f"{video_processing_average:.2f} ms"
    )

    print("\nAUDIO METRICS")
    print("-" * 40)
    print(f"Captured chunks: {metrics['audio_captured']}")
    print(f"Processed chunks: {metrics['audio_processed']}")
    print(f"Dropped chunks: {metrics['audio_dropped']}")
    print(f"Estimated audio duration: {audio_duration:.2f} seconds")
    print(
        "Audio chunk duration: "
        f"{(AUDIO_CHUNK / AUDIO_RATE) * 1000:.2f} ms"
    )
    print(
        "Audio queue high-water mark: "
        f"{metrics['audio_queue_peak']}"
    )
    print(
        "Average capture-to-processing latency: "
        f"{audio_average_latency:.2f} ms"
    )
    print(
        "Average audio processing time: "
        f"{audio_processing_average:.2f} ms"
    )

    print("\nSYSTEM METRICS")
    print("-" * 40)
    print(f"Process CPU time: {cpu_time:.2f} seconds")
    print(f"Average process CPU utilization: {cpu_utilization:.2f}%")

    print("\nQUEUE STATUS")
    print("-" * 40)
    print(f"Remaining video queue: {video_queue.qsize()}")
    print(f"Remaining audio queue: {audio_queue.qsize()}")

    print("\nTHREAD STATUS")
    print("-" * 40)
    print(f"Video capture thread alive: {video_thread.is_alive()}")
    print(f"Audio capture thread alive: {audio_thread.is_alive()}")
    print(f"Video processor alive: {video_processor.is_alive()}")
    print(f"Audio processor alive: {audio_processor.is_alive()}")

    print("\nOUTPUT FILES")
    print("-" * 40)
    print(f"Video exists: {VIDEO_OUTPUT.exists()}")
    print(f"Audio exists: {AUDIO_OUTPUT.exists()}")

    print("\n" + "=" * 55)


# --------------------------------------------------
# MAIN EXECUTION
# --------------------------------------------------

def main():
    camera = None
    audio = None
    stream = None
    video_writer = None
    audio_writer = None

    video_thread = None
    audio_thread = None
    video_processor = None
    audio_processor = None

    process_cpu_start = time.process_time()
    test_start = time.perf_counter()

    try:
        print("Initializing optimized capture system...")

        # Initialize webcam.
        camera = cv2.VideoCapture(CAMERA_INDEX)

        if not camera.isOpened():
            raise RuntimeError("Unable to open webcam.")

        camera.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
        camera.set(cv2.CAP_PROP_FPS, TARGET_FPS)

        actual_width = int(
            camera.get(cv2.CAP_PROP_FRAME_WIDTH)
        )
        actual_height = int(
            camera.get(cv2.CAP_PROP_FRAME_HEIGHT)
        )

        # Initialize video writer.
        fourcc = cv2.VideoWriter_fourcc(*"XVID")

        video_writer = cv2.VideoWriter(
            str(VIDEO_OUTPUT),
            fourcc,
            TARGET_FPS,
            (actual_width, actual_height)
        )

        if not video_writer.isOpened():
            raise RuntimeError("Unable to initialize video writer.")

        # Initialize microphone.
        audio = pyaudio.PyAudio()

        stream = audio.open(
            format=AUDIO_FORMAT,
            channels=AUDIO_CHANNELS,
            rate=AUDIO_RATE,
            input=True,
            frames_per_buffer=AUDIO_CHUNK
        )

        # Initialize WAV writer.
        audio_writer = wave.open(str(AUDIO_OUTPUT), "wb")
        audio_writer.setnchannels(AUDIO_CHANNELS)
        audio_writer.setsampwidth(
            audio.get_sample_size(AUDIO_FORMAT)
        )
        audio_writer.setframerate(AUDIO_RATE)

        print("Webcam initialized.")
        print("Microphone initialized.")
        print("Starting processing workers...")

        # Start processing workers first.
        video_processor = threading.Thread(
            target=video_processing_worker,
            args=(video_writer,),
            name="VideoProcessor"
        )

        audio_processor = threading.Thread(
            target=audio_processing_worker,
            args=(audio_writer,),
            name="AudioProcessor"
        )

        video_processor.start()
        audio_processor.start()

        # Start capture workers.
        video_thread = threading.Thread(
            target=video_capture_worker,
            args=(camera,),
            name="VideoCapture"
        )

        audio_thread = threading.Thread(
            target=audio_capture_worker,
            args=(stream,),
            name="AudioCapture"
        )

        video_thread.start()
        audio_thread.start()

        stream.start_stream()

        print("\nOptimized system is running.")
        print(f"Test duration: {TEST_DURATION} seconds")
        print("Press Q to stop early.\n")

        test_start = time.perf_counter()

        # Main thread handles only display and test duration.
        while not stop_event.is_set():
            elapsed = time.perf_counter() - test_start

            if elapsed >= TEST_DURATION:
                break

            with display_lock:
                frame_to_display = (
                    latest_frame.copy()
                    if latest_frame is not None
                    else None
                )

            if frame_to_display is not None:
                cv2.imshow(
                    "Day29 - Optimized System",
                    frame_to_display
                )

            if cv2.waitKey(10) & 0xFF == ord("q"):
                print("\nManual stop requested.")
                break

    except KeyboardInterrupt:
        print("\nKeyboard interruption received.")

    except Exception as error:
        print("\nExecution error:", error)

    finally:
        # ------------------------------------------
        # SHUTDOWN: STOP CAPTURE FIRST
        # ------------------------------------------

        stop_event.set()

        if stream is not None:
            try:
                if stream.is_active():
                    stream.stop_stream()
            except Exception as error:
                print("Audio stream shutdown warning:", error)

        # Wait until capture threads have completely stopped.
        # No processing sentinel is sent before this point.
        if video_thread is not None:
            video_thread.join()

        if audio_thread is not None:
            audio_thread.join()

        # ------------------------------------------
        # SHUTDOWN: DRAIN PROCESSING QUEUES
        # ------------------------------------------

        if video_processor is not None and video_processor.is_alive():
            video_queue.put(None)

        if audio_processor is not None and audio_processor.is_alive():
            audio_queue.put(None)

        if video_processor is not None:
            video_processor.join()

        if audio_processor is not None:
            audio_processor.join()

        # ------------------------------------------
        # RELEASE RESOURCES AFTER WORKERS STOP
        # ------------------------------------------

        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass

        if audio is not None:
            audio.terminate()

        if camera is not None:
            camera.release()

        if video_writer is not None:
            video_writer.release()

        if audio_writer is not None:
            audio_writer.close()

        cv2.destroyAllWindows()

        test_end = time.perf_counter()
        process_cpu_end = time.process_time()

        actual_duration = max(
            0,
            test_end - test_start
        )

        if all(
            worker is None or not worker.is_alive()
            for worker in (
                video_thread,
                audio_thread,
                video_processor,
                audio_processor
            )
        ):
            save_metrics(
                actual_duration,
                process_cpu_start,
                process_cpu_end,
                video_thread,
                audio_thread,
                video_processor,
                audio_processor
            )


if __name__ == "__main__":
    main()