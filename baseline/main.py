import cv2
import pyaudio
import threading
import queue
import time
import wave
import os

# -------------------- CONFIGURATION --------------------

CAMERA_INDEX = 0
MICROPHONE_INDEX = 1

AUDIO_RATE = 44100
AUDIO_CHANNELS = 1
AUDIO_FORMAT = pyaudio.paInt16
AUDIO_CHUNK = 1024

VIDEO_QUEUE_SIZE = 500
AUDIO_QUEUE_SIZE = 1000

VIDEO_FPS_FALLBACK = 30.0
TEST_DURATION = 60

OUTPUT_DIR = os.path.join("baseline", "output")
VIDEO_OUTPUT = os.path.join(OUTPUT_DIR, "baseline_video.mp4")
AUDIO_OUTPUT = os.path.join(OUTPUT_DIR, "baseline_audio.wav")
RESULT_FILE = os.path.join("results", "baseline_metrics.txt")

# -------------------------------------------------------

video_queue = queue.Queue(maxsize=VIDEO_QUEUE_SIZE)
audio_queue = queue.Queue(maxsize=AUDIO_QUEUE_SIZE)

stop_event = threading.Event()
start_event = threading.Event()

video_info = {"fps": VIDEO_FPS_FALLBACK}

metrics = {
    "video_captured": 0,
    "audio_captured": 0,
    "video_processed": 0,
    "audio_processed": 0,
    "video_queue_high": 0,
    "audio_queue_high": 0,
    "video_enqueue_wait": 0.0,
    "audio_enqueue_wait": 0.0,
    "video_latency_total": 0.0,
    "audio_latency_total": 0.0,
    "video_latency_samples": 0,
    "audio_latency_samples": 0,
}

metrics_lock = threading.Lock()


def update_queue_high_water(data_queue, metric_name):
    current_size = data_queue.qsize()

    with metrics_lock:
        metrics[metric_name] = max(
            metrics[metric_name],
            current_size
        )


def enqueue_without_discarding(data_queue, data, wait_metric):
    """Preserve every captured item by waiting for queue space."""

    wait_start = time.perf_counter()

    while not stop_event.is_set():
        try:
            data_queue.put(data, timeout=0.1)

            wait_time = time.perf_counter() - wait_start

            with metrics_lock:
                metrics[wait_metric] += wait_time

            update_queue_high_water(
                data_queue,
                "video_queue_high"
                if data_queue is video_queue
                else "audio_queue_high"
            )

            return True

        except queue.Full:
            continue

    return False


def webcam_capture(camera):
    print("[WEBCAM] Capture thread ready.")

    try:
        start_event.wait()

        while not stop_event.is_set():
            success, frame = camera.read()

            if not success:
                print("\n[WEBCAM] Frame capture failed.")
                stop_event.set()
                break

            capture_timestamp = time.perf_counter()

            with metrics_lock:
                metrics["video_captured"] += 1

            item = (frame, capture_timestamp)

            if not enqueue_without_discarding(
                video_queue,
                item,
                "video_enqueue_wait"
            ):
                break

    except Exception as error:
        print("\n[WEBCAM] Error:", error)
        stop_event.set()


def microphone_capture(stream):
    print("[AUDIO] Capture thread ready.")

    try:
        start_event.wait()

        while not stop_event.is_set():
            audio_data = stream.read(
                AUDIO_CHUNK,
                exception_on_overflow=False
            )

            capture_timestamp = time.perf_counter()

            with metrics_lock:
                metrics["audio_captured"] += 1

            item = (audio_data, capture_timestamp)

            if not enqueue_without_discarding(
                audio_queue,
                item,
                "audio_enqueue_wait"
            ):
                break

    except Exception as error:
        print("\n[AUDIO] Error:", error)
        stop_event.set()


def main():

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs("results", exist_ok=True)

    camera = None
    audio = None
    stream = None

    webcam_thread = None
    audio_thread = None

    video_writer = None
    audio_writer = None

    latest_frame = None

    process_cpu_start = time.process_time()
    wall_start = None

    print("\nDAY 29 - SYSTEM OPTIMIZATIONS")
    print("BASELINE PERFORMANCE TEST")
    print("Test duration:", TEST_DURATION, "seconds")
    print("Initializing webcam and microphone...\n")

    def process_queues():

        nonlocal video_writer
        nonlocal audio_writer
        nonlocal latest_frame

        while True:
            try:
                frame, capture_timestamp = video_queue.get_nowait()
            except queue.Empty:
                break

            processing_timestamp = time.perf_counter()

            with metrics_lock:
                metrics["video_processed"] += 1
                metrics["video_latency_total"] += (
                    processing_timestamp - capture_timestamp
                )
                metrics["video_latency_samples"] += 1

            if video_writer is None:
                height, width = frame.shape[:2]

                fourcc = cv2.VideoWriter_fourcc(*"mp4v")

                video_writer = cv2.VideoWriter(
                    VIDEO_OUTPUT,
                    fourcc,
                    video_info["fps"],
                    (width, height)
                )

                if not video_writer.isOpened():
                    raise RuntimeError("Could not create baseline video.")

                print("[VIDEO] Recording started.")

            video_writer.write(frame)
            latest_frame = frame

        while True:
            try:
                audio_data, capture_timestamp = audio_queue.get_nowait()
            except queue.Empty:
                break

            processing_timestamp = time.perf_counter()

            with metrics_lock:
                metrics["audio_processed"] += 1
                metrics["audio_latency_total"] += (
                    processing_timestamp - capture_timestamp
                )
                metrics["audio_latency_samples"] += 1

            if audio_writer is None:
                audio_writer = wave.open(AUDIO_OUTPUT, "wb")
                audio_writer.setnchannels(AUDIO_CHANNELS)
                audio_writer.setsampwidth(
                    audio.get_sample_size(AUDIO_FORMAT)
                )
                audio_writer.setframerate(AUDIO_RATE)

                print("[AUDIO] Recording started.")

            audio_writer.writeframes(audio_data)

    try:
        camera = cv2.VideoCapture(CAMERA_INDEX)

        if not camera.isOpened():
            raise RuntimeError("Could not open webcam.")

        width = int(camera.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT))

        camera_fps = camera.get(cv2.CAP_PROP_FPS)

        if 1 <= camera_fps <= 120:
            video_info["fps"] = camera_fps

        print("[WEBCAM] Resolution:", width, "x", height)
        print("[WEBCAM] FPS:", video_info["fps"])

        audio = pyaudio.PyAudio()

        stream = audio.open(
            format=AUDIO_FORMAT,
            channels=AUDIO_CHANNELS,
            rate=AUDIO_RATE,
            input=True,
            input_device_index=MICROPHONE_INDEX,
            frames_per_buffer=AUDIO_CHUNK
        )

        print("[AUDIO] Microphone initialized.")

        webcam_thread = threading.Thread(
            target=webcam_capture,
            args=(camera,),
            name="WebcamThread"
        )

        audio_thread = threading.Thread(
            target=microphone_capture,
            args=(stream,),
            name="MicrophoneThread"
        )

        webcam_thread.start()
        audio_thread.start()

        print("\nBoth devices initialized.")
        print("Starting baseline capture.")
        print("Press Q or ESC to stop early.\n")

        recording_start_time = time.perf_counter()
        wall_start = recording_start_time

        start_event.set()

        while not stop_event.is_set():

            elapsed = time.perf_counter() - recording_start_time

            if elapsed >= TEST_DURATION:
                print("\nBaseline duration completed.")
                break

            process_queues()

            if latest_frame is not None:
                display_frame = latest_frame.copy()

                cv2.putText(
                    display_frame,
                    "DAY 29 - BASELINE",
                    (20, 35),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 255, 0),
                    2
                )

                cv2.putText(
                    display_frame,
                    f"Elapsed: {elapsed:.1f}s",
                    (20, 70),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 255, 0),
                    2
                )

                cv2.imshow("Day29 Baseline", display_frame)

            print(
                f"\rFrames: {metrics['video_processed']} | "
                f"Audio chunks: {metrics['audio_processed']} | "
                f"Elapsed: {elapsed:.1f}s",
                end="",
                flush=True
            )

            key = cv2.waitKey(1) & 0xFF

            if key == ord("q") or key == 27:
                print("\nManual stop requested.")
                break

            time.sleep(0.001)

    except KeyboardInterrupt:
        print("\nKeyboard interrupt received.")

    except Exception as error:
        print("\n[MAIN] Error:", error)

    finally:
        stop_event.set()
        start_event.set()

        print("\nStopping capture threads...")

        # Stop microphone stream to release any blocked read.
        if stream is not None:
            try:
                if stream.is_active():
                    stream.stop_stream()
            except Exception:
                pass

        # Wait for capture threads.
        if webcam_thread is not None:
            webcam_thread.join(timeout=5)

        if audio_thread is not None:
            audio_thread.join(timeout=5)

        # Process all remaining queued data.
        try:
            process_queues()
        except Exception as error:
            print("[FINAL PROCESSING ERROR]", error)

        # Release devices.
        if camera is not None:
            camera.release()

        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass

        if audio is not None:
            audio.terminate()

        if video_writer is not None:
            video_writer.release()

        if audio_writer is not None:
            audio_writer.close()

        cv2.destroyAllWindows()

        wall_end = time.perf_counter()

        elapsed_total = (
            wall_end - wall_start if wall_start is not None else 0
        )

        process_cpu = time.process_time() - process_cpu_start

        video_count = metrics["video_processed"]
        audio_count = metrics["audio_processed"]

        average_fps = (
            video_count / elapsed_total if elapsed_total > 0 else 0
        )

        audio_duration = (
            audio_count * AUDIO_CHUNK / AUDIO_RATE
        )

        average_video_latency = (
            metrics["video_latency_total"]
            / metrics["video_latency_samples"]
            if metrics["video_latency_samples"] else 0
        )

        average_audio_latency = (
            metrics["audio_latency_total"]
            / metrics["audio_latency_samples"]
            if metrics["audio_latency_samples"] else 0
        )

        cpu_percentage = (
            process_cpu / elapsed_total * 100
            if elapsed_total > 0 else 0
        )

        results = [
            "DAY 29 - BASELINE PERFORMANCE RESULTS",
            "=" * 45,
            f"Test duration: {elapsed_total:.2f} seconds",
            f"Configured duration: {TEST_DURATION} seconds",
            "",
            "VIDEO METRICS",
            f"Captured frames: {metrics['video_captured']}",
            f"Processed frames: {video_count}",
            f"Average processing FPS: {average_fps:.2f}",
            f"Video queue high-water mark: {metrics['video_queue_high']}",
            f"Average capture-to-processing latency: {average_video_latency * 1000:.2f} ms",
            f"Video enqueue wait: {metrics['video_enqueue_wait']:.4f} seconds",
            "",
            "AUDIO METRICS",
            f"Captured chunks: {metrics['audio_captured']}",
            f"Processed chunks: {audio_count}",
            f"Estimated audio duration: {audio_duration:.2f} seconds",
            f"Audio chunk duration: {AUDIO_CHUNK / AUDIO_RATE * 1000:.2f} ms",
            f"Audio queue high-water mark: {metrics['audio_queue_high']}",
            f"Average capture-to-processing latency: {average_audio_latency * 1000:.2f} ms",
            f"Audio enqueue wait: {metrics['audio_enqueue_wait']:.4f} seconds",
            "",
            "SYSTEM METRICS",
            f"Process CPU time: {process_cpu:.2f} seconds",
            f"Average process CPU utilization: {cpu_percentage:.2f}%",
            "",
            "QUEUE STATUS",
            f"Remaining video queue: {video_queue.qsize()}",
            f"Remaining audio queue: {audio_queue.qsize()}",
            "",
            "OUTPUT FILES",
            f"Video exists: {os.path.exists(VIDEO_OUTPUT)}",
            f"Audio exists: {os.path.exists(AUDIO_OUTPUT)}",
        ]

        report = "\n".join(results)

        print("\n" + report)

        with open(RESULT_FILE, "w", encoding="utf-8") as file:
            file.write(report)

        print("\nBaseline results saved to:", RESULT_FILE)
        print("Baseline test completed.")


if __name__ == "__main__":
    main()