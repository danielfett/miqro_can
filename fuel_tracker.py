"""Fuel usage tracking module for miqro_can service."""

import csv
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from enum import Enum


class FuelType(Enum):
    """Supported fuel types."""
    DIESEL = "Diesel"
    HVO100 = "HVO100"
    PREMIUM_DIESEL = "Premium Diesel"
    
    @classmethod
    def from_string(cls, value: str) -> "FuelType":
        """Convert string to FuelType enum."""
        value_upper = value.upper().replace(" ", "_")
        try:
            return cls[value_upper]
        except KeyError:
            raise ValueError(f"Unknown fuel type: {value}")


class FuelEntry:
    """Represents a single fuel entry (refueling)."""
    
    def __init__(
        self,
        odometer: float,
        liters: float,
        cost: float,
        fuel_type: FuelType = FuelType.DIESEL,
        partial: bool = False,
        timestamp: Optional[datetime] = None,
    ):
        """Initialize a fuel entry.
        
        Args:
            odometer: Odometer reading in km
            liters: Amount of fuel added in liters
            cost: Cost of fuel in currency units
            fuel_type: Type of fuel (default: DIESEL)
            partial: Whether this is a partial refuel (default: False)
            timestamp: Time of refueling (default: now)
        """
        self.odometer = float(odometer)
        self.liters = float(liters)
        self.cost = float(cost)
        self.fuel_type = fuel_type
        self.partial = bool(partial)
        self.timestamp = timestamp or datetime.now()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert entry to dictionary for CSV storage."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "odometer": self.odometer,
            "liters": self.liters,
            "cost": self.cost,
            "fuel_type": self.fuel_type.value,
            "partial": str(self.partial),
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FuelEntry":
        """Create FuelEntry from dictionary (loaded from CSV)."""
        return cls(
            odometer=float(data["odometer"]),
            liters=float(data["liters"]),
            cost=float(data["cost"]),
            fuel_type=FuelType.from_string(data["fuel_type"]),
            partial=data.get("partial", "False").lower() in ["true", "1", "yes"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
        )


class FuelTracker:
    """Manages fuel consumption tracking and CSV storage."""
    
    CSV_COLUMNS = ["timestamp", "odometer", "liters", "cost", "fuel_type", "partial"]
    
    def __init__(self, csv_path: str):
        """Initialize fuel tracker.
        
        Args:
            csv_path: Path to CSV file for storing fuel data
        """
        self.csv_path = Path(csv_path)
        self.entries: List[FuelEntry] = []
        self._ensure_csv_exists()
        self._load_entries()
    
    def _ensure_csv_exists(self) -> None:
        """Ensure CSV file exists with headers."""
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        
        if not self.csv_path.exists():
            with open(self.csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=self.CSV_COLUMNS)
                writer.writeheader()
    
    def _load_entries(self) -> None:
        """Load existing entries from CSV file."""
        self.entries = []
        try:
            with open(self.csv_path, "r", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    self.entries.append(FuelEntry.from_dict(row))
        except (IOError, ValueError) as e:
            # Log error but continue - file might be empty or corrupted
            pass
    
    def add_entry(self, entry: FuelEntry) -> None:
        """Add a new fuel entry and save to CSV.
        
        Args:
            entry: FuelEntry to add
        """
        self.entries.append(entry)
        self._save_entry(entry)
    
    def _save_entry(self, entry: FuelEntry) -> None:
        """Append a single entry to the CSV file."""
        with open(self.csv_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.CSV_COLUMNS)
            writer.writerow(entry.to_dict())
    
    def get_entries(self) -> List[FuelEntry]:
        """Get all fuel entries."""
        return self.entries.copy()
    
    def get_entries_since(self, timestamp: datetime) -> List[FuelEntry]:
        """Get fuel entries since a specific timestamp.
        
        Args:
            timestamp: Filter entries after this time
            
        Returns:
            List of FuelEntry objects
        """
        return [e for e in self.entries if e.timestamp >= timestamp]
    
    def get_entries_by_type(self, fuel_type: FuelType) -> List[FuelEntry]:
        """Get fuel entries for a specific fuel type.
        
        Args:
            fuel_type: FuelType to filter by
            
        Returns:
            List of FuelEntry objects
        """
        return [e for e in self.entries if e.fuel_type == fuel_type]
    
    def get_full_refuels_only(self) -> List[FuelEntry]:
        """Get only full refuel entries (not partial).
        
        Returns:
            List of full FuelEntry objects
        """
        return [e for e in self.entries if not e.partial]
    
    def get_last_entry(self) -> Optional[FuelEntry]:
        """Get the most recent fuel entry.
        
        Returns:
            Last FuelEntry or None if no entries exist
        """
        return self.entries[-1] if self.entries else None
