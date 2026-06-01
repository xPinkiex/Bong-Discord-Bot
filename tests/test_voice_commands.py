import pytest
import numpy as np
import struct
import wave
import io
from unittest.mock import MagicMock, patch, AsyncMock

import voice_commands
from voice_commands import (
    BongVoiceSink,
    SAMPLE_RATE,
    SAMPLE_WIDTH,
    CHANNELS,
    WHISPER_SAMPLE_RATE,
    WHISPER_CHANNELS,
    WAKE_WORD,
    FRAME_SIZE,
    OWW_FRAME_BYTES,
)


def _activate_sink_user(sink, user_id):
    sink._activated[user_id] = True


class TestResampleToWhisperFormat:

    def _make_stereo_pcm(self, duration_s: float, frequency: int = 440) -> bytes:
        num_samples = int(SAMPLE_RATE * duration_s)
        t = np.arange(num_samples, dtype=np.float64) / SAMPLE_RATE
        signal = (np.sin(2 * np.pi * frequency * t) * 16000).astype(np.int16)
        stereo = np.column_stack((signal, signal))
        return stereo.tobytes()

    def test_output_is_mono(self):
        pcm = self._make_stereo_pcm(1.0)
        sink = BongVoiceSink(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        result = sink._resample_to_whisper_format(pcm)
        result_array = np.frombuffer(result, dtype=np.int16)
        assert len(result_array) > 0

    def test_downsample_ratio(self):
        duration = 1.0
        pcm = self._make_stereo_pcm(duration)
        expected_target_frames = int(WHISPER_SAMPLE_RATE * duration)
        sink = BongVoiceSink(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        result = sink._resample_to_whisper_format(pcm)
        result_array = np.frombuffer(result, dtype=np.int16)
        assert abs(len(result_array) - expected_target_frames) <= 2

    def test_short_audio(self):
        pcm = self._make_stereo_pcm(0.1)
        sink = BongVoiceSink(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        result = sink._resample_to_whisper_format(pcm)
        assert len(result) > 0

    def test_empty_audio(self):
        sink = BongVoiceSink(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        result = sink._resample_to_whisper_format(b'')
        assert len(result) == 0


class TestFlushUtterance:

    def test_too_short_returns_none(self):
        sink = BongVoiceSink(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        num_frames = int(SAMPLE_RATE * 0.1)
        fake_pcm = b'\x00' * (num_frames * SAMPLE_WIDTH * CHANNELS)
        sink._buffers[123] = bytearray(fake_pcm)
        result = sink._flush_utterance(123)
        assert result is None

    def test_long_enough_returns_data(self):
        sink = BongVoiceSink(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        num_frames = int(SAMPLE_RATE * 1.0)
        fake_pcm = b'\x00' * (num_frames * SAMPLE_WIDTH * CHANNELS)
        sink._buffers[123] = bytearray(fake_pcm)
        result = sink._flush_utterance(123)
        assert result is not None
        assert len(result) == len(fake_pcm)

    def test_flush_clears_buffer(self):
        sink = BongVoiceSink(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        num_frames = int(SAMPLE_RATE * 0.5)
        fake_pcm = b'\x00' * (num_frames * SAMPLE_WIDTH * CHANNELS)
        sink._buffers[123] = bytearray(fake_pcm)
        sink._flush_utterance(123)
        assert len(sink._buffers[123]) == 0

    def test_empty_buffer_returns_none(self):
        sink = BongVoiceSink(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        result = sink._flush_utterance(999)
        assert result is None


class TestWakeWordDetection:

    @pytest.mark.asyncio
    async def test_wake_word_found(self):
        sink = BongVoiceSink(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        sink.guild = MagicMock()
        sink.guild.id = 111

        calls = []
        async def mock_process(bot, guild, channel, user_id, username, text):
            calls.append(text)

        with patch('voice_commands.user_data.is_authorized', return_value=True):
            with patch('bong.process_voice_command', side_effect=mock_process):
                await sink._handle_transcription(222, "hey bong what time is it")

        assert len(calls) == 1
        assert calls[0] == "what time is it"

    @pytest.mark.asyncio
    async def test_wake_word_not_found_passes_through(self):
        sink = BongVoiceSink(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        sink.guild = MagicMock()

        with patch('voice_commands.user_data.is_authorized', return_value=True):
            with patch('bong.process_voice_command') as mock_process:
                await sink._handle_transcription(222, "hello what time is it")
                mock_process.assert_called_once()

    @pytest.mark.asyncio
    async def test_bong_wake_word_stripped(self):
        sink = BongVoiceSink(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        sink.guild = MagicMock()

        calls = []
        async def mock_process(bot, guild, channel, user_id, username, text):
            calls.append(text)

        with patch('voice_commands.user_data.is_authorized', return_value=True):
            with patch('bong.process_voice_command', side_effect=mock_process):
                await sink._handle_transcription(222, "hey bong, play music")

        assert len(calls) == 1
        assert calls[0] == "play music"

    @pytest.mark.asyncio
    async def test_wake_word_only(self):
        sink = BongVoiceSink(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        sink.guild = MagicMock()

        calls = []
        async def mock_process(bot, guild, channel, user_id, username, text):
            calls.append(text)

        with patch('voice_commands.user_data.is_authorized', return_value=True):
            with patch('bong.process_voice_command', side_effect=mock_process):
                await sink._handle_transcription(222, "hey bong")

        assert len(calls) == 1
        assert calls[0] == "Hey"

    @pytest.mark.asyncio
    async def test_wake_word_strips_punctuation(self):
        sink = BongVoiceSink(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        sink.guild = MagicMock()

        calls = []
        async def mock_process(bot, guild, channel, user_id, username, text):
            calls.append(text)

        with patch('voice_commands.user_data.is_authorized', return_value=True):
            with patch('bong.process_voice_command', side_effect=mock_process):
                await sink._handle_transcription(222, "hey bong, play music")

        assert len(calls) == 1
        assert calls[0] == "play music"

    @pytest.mark.asyncio
    async def test_case_insensitive(self):
        sink = BongVoiceSink(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        sink.guild = MagicMock()

        calls = []
        async def mock_process(bot, guild, channel, user_id, username, text):
            calls.append(text)

        with patch('voice_commands.user_data.is_authorized', return_value=True):
            with patch('bong.process_voice_command', side_effect=mock_process):
                await sink._handle_transcription(222, "Hey Bong play music")

        assert len(calls) == 1
        assert calls[0] == "play music"


class TestWriteBuffer:

    def _make_speech_frame(self, rms_value: int = 2000) -> bytes:
        frame = np.full(SAMPLE_RATE // 50 * CHANNELS, rms_value, dtype=np.int16)
        return frame.tobytes()

    def _make_silence_frame(self) -> bytes:
        frame = np.zeros(SAMPLE_RATE // 50 * CHANNELS, dtype=np.int16)
        return frame.tobytes()

    def test_speech_frames_are_buffered(self):
        sink = BongVoiceSink(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        user = MagicMock()
        user.id = 123
        user.bot = False
        _activate_sink_user(sink, 123)
        data = MagicMock()
        data.pcm = self._make_speech_frame(2000)

        sink.write(user, data)
        assert 123 in sink._buffers
        assert len(sink._buffers[123]) > 0

    def test_silence_frames_not_buffered_initially(self):
        sink = BongVoiceSink(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        user = MagicMock()
        user.id = 123
        user.bot = False
        data = MagicMock()
        data.pcm = self._make_silence_frame()

        sink.write(user, data)
        assert 123 not in sink._buffers or len(sink._buffers[123]) == 0

    def test_speech_frames_not_buffered_without_activation(self):
        sink = BongVoiceSink(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        user = MagicMock()
        user.id = 123
        user.bot = False
        data = MagicMock()
        data.pcm = self._make_speech_frame(2000)

        sink.write(user, data)
        assert 123 not in sink._buffers or len(sink._buffers[123]) == 0

    def test_bot_frames_ignored(self):
        sink = BongVoiceSink(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        user = MagicMock()
        user.id = 999
        user.bot = True
        data = MagicMock()
        data.pcm = self._make_speech_frame()

        sink.write(user, data)
        assert 999 not in sink._buffers

    def test_none_user_ignored(self):
        sink = BongVoiceSink(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        data = MagicMock()
        data.pcm = self._make_speech_frame()

        sink.write(None, data)
        assert len(sink._buffers) == 0

    def test_empty_pcm_ignored(self):
        sink = BongVoiceSink(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        user = MagicMock()
        user.id = 123
        user.bot = False
        _activate_sink_user(sink, 123)
        speech_data = MagicMock()
        speech_data.pcm = self._make_speech_frame(2000)
        sink.write(user, speech_data)

        empty_data = MagicMock()
        empty_data.pcm = b''
        sink.write(user, empty_data)

        # Empty pcm is now just skipped
        assert len(sink._buffers[123]) > 0

    def test_silence_after_speech_is_buffered(self):
        sink = BongVoiceSink(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        user = MagicMock()
        user.id = 123
        user.bot = False
        _activate_sink_user(sink, 123)

        speech_data = MagicMock()
        speech_data.pcm = self._make_speech_frame(2000)
        sink.write(user, speech_data)
        initial_len = len(sink._buffers[123])

        silence_data = MagicMock()
        silence_data.pcm = self._make_silence_frame()
        sink.write(user, silence_data)
        assert len(sink._buffers[123]) > initial_len

    def test_no_extension_data_accepted(self):
        sink = BongVoiceSink(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        user = MagicMock()
        user.id = 123
        user.bot = False
        _activate_sink_user(sink, 123)
        data = MagicMock()
        data.pcm = self._make_speech_frame(2000)
        data.packet = MagicMock()
        data.packet.extension_data = None

        sink.write(user, data)
        assert 123 in sink._buffers

    def test_empty_extension_data_accepted(self):
        sink = BongVoiceSink(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        user = MagicMock()
        user.id = 123
        user.bot = False
        _activate_sink_user(sink, 123)
        data = MagicMock()
        data.pcm = self._make_speech_frame(2000)
        data.packet = MagicMock()
        data.packet.extension_data = {}

        sink.write(user, data)
        assert 123 in sink._buffers


class TestStartStopListening:

    @pytest.mark.asyncio
    async def test_start_already_listening(self):
        voice_commands._is_listening[555] = True
        guild = MagicMock()
        guild.id = 555
        guild.voice_client = MagicMock()
        result = await voice_commands.start_listening(MagicMock(), guild, MagicMock())
        assert result == "Already listening for voice commands."
        del voice_commands._is_listening[555]

    @pytest.mark.asyncio
    async def test_stop_not_listening(self):
        voice_commands._is_listening.pop(555, None)
        result = await voice_commands.stop_listening(MagicMock())
        assert result == "Not currently listening for voice commands."

    @pytest.mark.asyncio
    async def test_stop_cleans_up(self):
        guild = MagicMock()
        guild.id = 555
        guild.voice_client = MagicMock()
        guild.voice_client.is_listening.return_value = True
        from discord.ext.voice_recv import VoiceRecvClient
        guild.voice_client.isinstance = lambda cls: isinstance(guild.voice_client, cls)

        voice_commands._is_listening[555] = True
        voice_commands._active_listeners[555] = MagicMock()
        voice_commands._active_listeners[555]._stopped = False
        voice_commands._active_listeners[555]._silence_task = MagicMock()
        voice_commands._active_listeners[555]._silence_task.done.return_value = True

        result = await voice_commands.stop_listening(guild)
        assert "Stopped" in result
        assert voice_commands._is_listening.get(555, False) is False


class TestIsListening:

    def test_not_listening(self):
        assert voice_commands.is_listening(999) is False

    def test_listening(self):
        voice_commands._is_listening[999] = True
        assert voice_commands.is_listening(999) is True
        del voice_commands._is_listening[999]


class TestWantsOpus:

    def test_wants_opus_returns_false(self):
        sink = BongVoiceSink(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        assert sink.wants_opus() is False


class TestWaveOutput:

    def test_resampled_data_is_valid_wav(self):
        duration = 1.0
        num_samples = int(SAMPLE_RATE * duration)
        t = np.arange(num_samples, dtype=np.float64) / SAMPLE_RATE
        signal = (np.sin(2 * np.pi * 440 * t) * 16000).astype(np.int16)
        stereo = np.column_stack((signal, signal))
        pcm = stereo.tobytes()

        sink = BongVoiceSink(MagicMock(), MagicMock(), MagicMock(), MagicMock())
        resampled = sink._resample_to_whisper_format(pcm)

        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(WHISPER_CHANNELS)
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(WHISPER_SAMPLE_RATE)
            wf.writeframes(resampled)
        wav_buffer.seek(0)

        with wave.open(wav_buffer, "rb") as wf:
            assert wf.getnchannels() == WHISPER_CHANNELS
            assert wf.getsampwidth() == SAMPLE_WIDTH
            assert wf.getframerate() == WHISPER_SAMPLE_RATE
            frames = wf.getnframes()
            assert abs(frames - WHISPER_SAMPLE_RATE) <= 2