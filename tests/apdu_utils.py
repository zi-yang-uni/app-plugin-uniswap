from dataclasses import dataclass
from typing import Optional, List
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class HexConverter:
    """Centralized hex string to bytes conversion with validation."""
    
    @staticmethod
    def to_bytes(hex_string: str) -> bytes:
        """
        Convert hex string to bytes with validation.
        
        Args:
            hex_string: Hex string (e.g., "e0000000ee4afbe...")
        
        Returns:
            Bytes representation
        
        Raises:
            ValueError: If hex string is malformed
        """
        try:
            cleaned = hex_string.strip().replace(" ", "")
            if not cleaned:
                raise ValueError("Empty hex string")
            
            if not all(c in "0123456789abcdefABCDEF" for c in cleaned):
                raise ValueError(f"Invalid hex characters in: {cleaned[:50]}...")
            
            return bytes.fromhex(cleaned)
        except ValueError as e:
            raise ValueError(f"Failed to convert hex: {hex_string[:50]}... - {e}")
    
    @staticmethod
    def to_display(data: bytes, chunk_size: int = 32) -> str:
        """Format bytes for readable logging (grouped by chunk_size bytes)."""
        hex_str = data.hex()
        return '\n'.join(hex_str[i:i+chunk_size*2] for i in range(0, len(hex_str), chunk_size*2))


@dataclass(frozen=True)
class APDUPair:
    """
    Represents a single APDU command-response pair.
    
    Attributes:
        command: Hex string of APDU command (None for standalone responses)
        response: Hex string of APDU response (status word + optional data)
        line_number: Original line number in apdus.txt for debugging
    """
    command: Optional[str]
    response: str
    line_number: int
    
    def __str__(self) -> str:
        cmd = f"CMD: {self.command[:40]}..." if self.command else "CMD: None"
        rsp = f"RSP: {self.response[:40]}..." if len(self.response) > 40 else f"RSP: {self.response}"
        return f"[Line {self.line_number}] {cmd}, {rsp}"


class APDUFileParser:
    """Robust parser for APDU trace files."""
    
    COMMAND_PREFIX = "=> "
    RESPONSE_PREFIX = "<= "
    
    @staticmethod
    def parse_file(filepath: Path) -> List[APDUPair]:
        """
        Parse APDU file into structured pairs.
        
        Handles:
        - Standalone responses (no preceding command)
        - Standard command-response pairs
        - Empty lines (skipped)
        - Malformed lines (logged, skipped)
        
        Args:
            filepath: Path to apdus.txt
        
        Returns:
            List of APDUPair objects in file order
        """
        pairs = []
        
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = [line.strip() for line in f if line.strip()]
        
        i = 0
        while i < len(lines):
            line = lines[i]
            
            if line.startswith(APDUFileParser.RESPONSE_PREFIX):
                response = line[3:]
                pairs.append(APDUPair(
                    command=None,
                    response=response,
                    line_number=i + 1
                ))
                i += 1
                
            elif line.startswith(APDUFileParser.COMMAND_PREFIX):
                command = line[3:]
                
                if i + 1 < len(lines) and lines[i + 1].startswith(APDUFileParser.RESPONSE_PREFIX):
                    response = lines[i + 1][3:]
                    pairs.append(APDUPair(
                        command=command,
                        response=response,
                        line_number=i + 1
                    ))
                    i += 2
                else:
                    logger.warning(f"Command at line {i+1} has no response, skipping")
                    i += 1
            else:
                logger.warning(f"Malformed line {i+1}: {line[:50]}")
                i += 1
        
        logger.info(f"Parsed {len(pairs)} APDU pairs from {filepath}")
        return pairs
    
    @staticmethod
    def validate_pairs(pairs: List[APDUPair]) -> bool:
        """
        Validate that parsed pairs are well-formed.
        
        Returns:
            True if all pairs valid, False otherwise
        """
        has_errors = False
        for pair in pairs:
            if pair.command and not pair.response:
                logger.error(f"Command at line {pair.line_number} has no response")
                has_errors = True
        return not has_errors