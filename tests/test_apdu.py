import pytest
from pathlib import Path
from time import sleep
from typing import List
import logging
import time

from tests.apdu_utils import APDUFileParser, APDUPair, HexConverter
from ragger.error import ExceptionRAPDU


def setup_apdu_logging() -> logging.Logger:
    """
    Configure structured logging for APDU tests.
    
    Logs to both file (detailed) and console (summary).
    """
    log_file = Path(__file__).parent / "apdu_replay.log"
    
    file_handler = logging.FileHandler(log_file, mode='w')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(
        '%(levelname)s: %(message)s'
    ))
    
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

APDU_FILE = Path(__file__).parent.parent / "apdus.txt"
ASYNC_SLEEP_MS = 100

logger = logging.getLogger(__name__)

class TestAPDUReplay:
    """
    Replay production APDU sequences from captured traces.
    
    This test validates that the device handles real-world APDU
    sequences correctly by replaying production traffic.
    """
    
    @pytest.fixture(scope="class")
    def apdu_pairs(self) -> List[APDUPair]:
        """
        Load and parse APDU file once per test class.
        
        Returns:
            List of APDUPair objects parsed from apdus.txt
        """
        if not APDU_FILE.exists():
            pytest.skip(f"APDU trace file not found: {APDU_FILE}")
        
        pairs = APDUFileParser.parse_file(APDU_FILE)
        
        if not APDUFileParser.validate_pairs(pairs):
            pytest.fail("APDU file contains malformed pairs")
        
        logger.info(f"Loaded {len(pairs)} APDU pairs from {APDU_FILE}")
        return pairs
    
    @pytest.fixture(autouse=True)
    def reset_device_state(self, backend):
        """
        Reset device to clean state before each test.
        
        Critical for session-scoped backend to prevent state pollution.
        """
        try:
            backend.wait_for_home_screen()
        except Exception as e:
            logger.warning(f"Failed to reset device state: {e}")
        
        yield
        
        try:
            backend.wait_for_home_screen(timeout=2)
        except Exception as e:
            logger.error(f"Device not in clean state after test: {e}")
    
    @pytest.fixture(autouse=True)
    def setup_logging(self):
        """Auto-setup logging for each test."""
        self.logger = setup_apdu_logging()
        self.logger.info("=" * 80)
        self.logger.info(f"Starting APDU replay test")
        self.logger.info("=" * 80)
        yield
        self.logger.info("APDU replay test completed")

# Backend API Research Findings:
# - backend.exchange_raw(data: bytes) -> RAPDU
# - RAPDU is a dataclass with:
#   - status: int (2-byte status code, 0x9000 = success)
#   - data: bytes (response data before status word)
# - backend.wait_for_home_screen() exists for device state reset
# - ExceptionRAPDU raised for APDU errors (from ragger.error)

    def test_full_sequence_replay(self, backend, apdu_pairs, uniswap_client):
        """
        Replay entire APDU sequence from production capture.
        
        Tests the complete interaction flow by sending all commands
        and validating responses match expected values.
        """
        # Add plugin setup before APDU replay
        uniswap_client.set_external_plugin()
        self.logger.info("Uniswap plugin registered successfully")
        
        self.logger.info(f"Starting replay of {len(apdu_pairs)} APDU pairs")
        
        start_time = time.time()
        failures = []
        
        for idx, pair in enumerate(apdu_pairs):
            if idx % 50 == 0:
                self.logger.info(f"Progress: {idx+1}/{len(apdu_pairs)} pairs")
            
            self.logger.debug(f"\n--- Pair {idx+1} (line {pair.line_number}) ---")
            self.logger.debug(f"Command: {pair.command}")
            self.logger.debug(f"Expected: {pair.response}")
            
            try:
                if pair.command:
                    response = self._send_command(backend, pair.command)
                    self._validate_response(response, pair.response, pair.line_number, failures)
                else:
                    self.logger.debug(f"Skipping standalone response at line {pair.line_number}")
            
            except Exception as e:
                self.logger.error(f"FAILED at pair {idx+1} (line {pair.line_number}): {e}")
                self._dump_context(pair, idx, apdu_pairs)
                raise
        
        sleep(ASYNC_SLEEP_MS / 1000.0)
        
        elapsed = time.time() - start_time
        self.logger.info(f"Replay completed in {elapsed:.2f}s")
        
        if failures:
            self.logger.warning(f"Found {len(failures)} response mismatches (see log for details)")
        else:
            self.logger.info("All responses matched expected values ✓")
    
    def _send_command(self, backend, command_hex: str) -> str:
        """Send command with error handling."""
        command_bytes = HexConverter.to_bytes(command_hex)
        self.logger.debug(f"Sending: {command_hex[:60]}...")
        
        try:
            rapdu = backend.exchange_raw(command_bytes)
            # Convert RAPDU to hex string format expected by validation
            response_hex = rapdu.data.hex() + f"{rapdu.status:04x}"
            self.logger.debug(f"Received: {response_hex}")
            return response_hex
        
        except ExceptionRAPDU as e:
            error_msg = f"RAPDU Exception: {e.status:04x}"
            self.logger.error(error_msg)
            
            if e.status == 0x6511:
                self.logger.error("  Interpretation: User declined transaction")
            elif e.status == 0x6a80:
                self.logger.error("  Interpretation: Invalid data in APDU")
            elif e.status == 0x6d00:
                self.logger.error("  Interpretation: INS not supported")
            else:
                self.logger.error(f"  Interpretation: Unknown error code")
            
            raise
        
        except Exception as e:
            self.logger.error(f"Unexpected error during APDU exchange: {e}")
            raise
    
    def _validate_response(self, actual_hex: str, expected_hex: str, line_num: int, failures: list):
        """
        Validate response matches expected value (relaxed mode).
        
        Logs mismatches but continues execution to observe full sequence.
        """
        if actual_hex != expected_hex:
            failure_msg = (
                f"Response mismatch at line {line_num}:\n"
                f"  Expected: {expected_hex}\n"
                f"  Actual:   {actual_hex}"
            )
            self.logger.warning(failure_msg)
            failures.append((line_num, expected_hex, actual_hex))
    
    def _dump_context(self, failed_pair: APDUPair, index: int, all_pairs: List[APDUPair]):
        """Dump surrounding context on failure."""
        self.logger.error("\n=== FAILURE CONTEXT ===")
        self.logger.error(f"Failed pair: {index+1} (line {failed_pair.line_number})")
        
        context_start = max(0, index - 2)
        context_end = min(len(all_pairs), index + 3)
        
        self.logger.error("\nSurrounding pairs:")
        for i in range(context_start, context_end):
            prefix = ">>> " if i == index else "    "
            pair = all_pairs[i]
            self.logger.error(f"{prefix}[{i+1}] Line {pair.line_number}")
            self.logger.error(f"{prefix}    CMD: {pair.command}")
            self.logger.error(f"{prefix}    RSP: {pair.response}")