"""
Unit tests and examples for fuel tracking modules.

Run with: python -m pytest fuel_tests.py
"""

import pytest
import tempfile
import os
from datetime import datetime, timedelta
from pathlib import Path
from fuel_tracker import FuelTracker, FuelEntry, FuelType
from fuel_stats import FuelStatistics


class TestFuelType:
    """Test FuelType enum functionality."""
    
    def test_fuel_type_values(self):
        """Test fuel type values."""
        assert FuelType.DIESEL.value == "Diesel"
        assert FuelType.HVO100.value == "HVO100"
        assert FuelType.PREMIUM_DIESEL.value == "Premium Diesel"
    
    def test_fuel_type_from_string(self):
        """Test creating FuelType from string."""
        assert FuelType.from_string("Diesel") == FuelType.DIESEL
        assert FuelType.from_string("diesel") == FuelType.DIESEL
        assert FuelType.from_string("HVO100") == FuelType.HVO100
        assert FuelType.from_string("Premium Diesel") == FuelType.PREMIUM_DIESEL
        assert FuelType.from_string("PREMIUM_DIESEL") == FuelType.PREMIUM_DIESEL
    
    def test_fuel_type_invalid(self):
        """Test invalid fuel type."""
        with pytest.raises(ValueError):
            FuelType.from_string("Unknown")


class TestFuelEntry:
    """Test FuelEntry class."""
    
    def test_fuel_entry_creation(self):
        """Test creating a fuel entry."""
        entry = FuelEntry(
            odometer=45000,
            liters=50.5,
            cost=72.50,
            fuel_type=FuelType.DIESEL,
            partial=False,
        )
        
        assert entry.odometer == 45000.0
        assert entry.liters == 50.5
        assert entry.cost == 72.50
        assert entry.fuel_type == FuelType.DIESEL
        assert entry.partial is False
        assert isinstance(entry.timestamp, datetime)
    
    def test_fuel_entry_to_dict(self):
        """Test converting entry to dictionary."""
        timestamp = datetime(2025, 1, 5, 10, 30, 0)
        entry = FuelEntry(
            odometer=45000,
            liters=50.5,
            cost=72.50,
            fuel_type=FuelType.DIESEL,
            partial=False,
            timestamp=timestamp,
        )
        
        data = entry.to_dict()
        assert data["odometer"] == 45000.0
        assert data["liters"] == 50.5
        assert data["cost"] == 72.50
        assert data["fuel_type"] == "Diesel"
        assert data["partial"] == "False"
        assert data["timestamp"] == timestamp.isoformat()
    
    def test_fuel_entry_from_dict(self):
        """Test creating entry from dictionary."""
        timestamp = datetime(2025, 1, 5, 10, 30, 0)
        data = {
            "odometer": "45000.0",
            "liters": "50.5",
            "cost": "72.50",
            "fuel_type": "Diesel",
            "partial": "False",
            "timestamp": timestamp.isoformat(),
        }
        
        entry = FuelEntry.from_dict(data)
        assert entry.odometer == 45000.0
        assert entry.liters == 50.5
        assert entry.cost == 72.50
        assert entry.fuel_type == FuelType.DIESEL
        assert entry.partial is False


class TestFuelTracker:
    """Test FuelTracker class."""
    
    def test_tracker_initialization(self):
        """Test creating a fuel tracker."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "fuel.csv")
            tracker = FuelTracker(csv_path)
            
            assert Path(csv_path).exists()
            assert tracker.get_entries() == []
    
    def test_add_entry(self):
        """Test adding entries to tracker."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "fuel.csv")
            tracker = FuelTracker(csv_path)
            
            entry = FuelEntry(45000, 50.5, 72.50, FuelType.DIESEL)
            tracker.add_entry(entry)
            
            entries = tracker.get_entries()
            assert len(entries) == 1
            assert entries[0].odometer == 45000.0
    
    def test_load_entries_from_csv(self):
        """Test loading entries from existing CSV."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "fuel.csv")
            
            # Create first tracker and add entry
            tracker1 = FuelTracker(csv_path)
            entry = FuelEntry(45000, 50.5, 72.50, FuelType.DIESEL)
            tracker1.add_entry(entry)
            
            # Create second tracker - should load the entry
            tracker2 = FuelTracker(csv_path)
            entries = tracker2.get_entries()
            assert len(entries) == 1
    
    def test_get_entries_by_type(self):
        """Test filtering entries by fuel type."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "fuel.csv")
            tracker = FuelTracker(csv_path)
            
            tracker.add_entry(FuelEntry(45000, 50.5, 72.50, FuelType.DIESEL))
            tracker.add_entry(FuelEntry(45100, 48.0, 69.60, FuelType.HVO100))
            tracker.add_entry(FuelEntry(45200, 52.0, 78.00, FuelType.DIESEL))
            
            diesel = tracker.get_entries_by_type(FuelType.DIESEL)
            assert len(diesel) == 2
            
            hvo = tracker.get_entries_by_type(FuelType.HVO100)
            assert len(hvo) == 1
    
    def test_get_full_refuels_only(self):
        """Test filtering full refuels."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "fuel.csv")
            tracker = FuelTracker(csv_path)
            
            tracker.add_entry(FuelEntry(45000, 50.5, 72.50, FuelType.DIESEL, partial=False))
            tracker.add_entry(FuelEntry(45100, 10.0, 14.50, FuelType.DIESEL, partial=True))
            tracker.add_entry(FuelEntry(45200, 52.0, 78.00, FuelType.DIESEL, partial=False))
            
            full_only = tracker.get_full_refuels_only()
            assert len(full_only) == 2
            assert all(not e.partial for e in full_only)


class TestFuelStatistics:
    """Test FuelStatistics class."""
    
    def test_average_consumption(self):
        """Test calculating average fuel consumption."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "fuel.csv")
            tracker = FuelTracker(csv_path)
            
            # First refuel at 45000 km with 50L
            tracker.add_entry(FuelEntry(45000, 50.0, 70.0, FuelType.DIESEL, partial=False))
            
            # Second refuel at 45500 km with 50L (500 km on 50L)
            tracker.add_entry(FuelEntry(45500, 50.0, 70.0, FuelType.DIESEL, partial=False))
            
            stats = FuelStatistics(tracker)
            consumption = stats.get_average_consumption(liters_per_100km=True)
            
            # Should be 50L / 500km * 100 = 10 l/100km
            assert consumption == pytest.approx(10.0, rel=0.01)
    
    def test_average_consumption_insufficient_data(self):
        """Test that average consumption returns None with insufficient data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "fuel.csv")
            tracker = FuelTracker(csv_path)
            
            # Only one entry
            tracker.add_entry(FuelEntry(45000, 50.0, 70.0, FuelType.DIESEL))
            
            stats = FuelStatistics(tracker)
            consumption = stats.get_average_consumption()
            
            assert consumption is None
    
    def test_consumption_by_fuel_type(self):
        """Test consumption breakdown by fuel type."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "fuel.csv")
            tracker = FuelTracker(csv_path)
            
            # Diesel: 45000 to 45500 km on 50L = 10 l/100km
            tracker.add_entry(FuelEntry(45000, 50.0, 70.0, FuelType.DIESEL, partial=False))
            tracker.add_entry(FuelEntry(45500, 50.0, 70.0, FuelType.DIESEL, partial=False))
            
            # HVO100: 45500 to 46000 km on 50L = 10 l/100km
            tracker.add_entry(FuelEntry(45500, 50.0, 75.0, FuelType.HVO100, partial=False))
            tracker.add_entry(FuelEntry(46000, 50.0, 75.0, FuelType.HVO100, partial=False))
            
            stats = FuelStatistics(tracker)
            by_type = stats.get_consumption_by_fuel_type(liters_per_100km=True)
            
            assert by_type["Diesel"] == pytest.approx(10.0, rel=0.01)
            assert by_type["HVO100"] == pytest.approx(10.0, rel=0.01)
            assert by_type["Premium Diesel"] is None
    
    def test_cost_per_liter_by_type(self):
        """Test cost per liter calculation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "fuel.csv")
            tracker = FuelTracker(csv_path)
            
            # Diesel: 50L for 70 currency = 1.40/L
            tracker.add_entry(FuelEntry(45000, 50.0, 70.0, FuelType.DIESEL))
            
            # HVO100: 50L for 75 currency = 1.50/L
            tracker.add_entry(FuelEntry(45500, 50.0, 75.0, FuelType.HVO100))
            
            stats = FuelStatistics(tracker)
            cost_per_l = stats.get_cost_per_liter_by_type()
            
            assert cost_per_l["Diesel"] == pytest.approx(1.40, rel=0.01)
            assert cost_per_l["HVO100"] == pytest.approx(1.50, rel=0.01)
    
    def test_statistics_summary(self):
        """Test getting comprehensive statistics summary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "fuel.csv")
            tracker = FuelTracker(csv_path)
            
            tracker.add_entry(FuelEntry(45000, 50.0, 70.0, FuelType.DIESEL, partial=False))
            tracker.add_entry(FuelEntry(45500, 50.0, 70.0, FuelType.DIESEL, partial=False))
            
            stats = FuelStatistics(tracker)
            summary = stats.get_statistics_summary(current_odometer=45500)
            
            assert "average_consumption_l_per_100km" in summary
            assert "consumption_by_fuel_type" in summary
            assert "cost_per_liter_by_type" in summary
            assert "total_entries" in summary
            assert summary["total_entries"] == 2
            assert summary["full_refuels"] == 2


class TestIntegration:
    """Integration tests."""
    
    def test_full_workflow(self):
        """Test a complete workflow of adding entries and calculating stats."""
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "fuel.csv")
            tracker = FuelTracker(csv_path)
            stats = FuelStatistics(tracker)
            
            # First trip: 500km on 50L of Diesel
            tracker.add_entry(FuelEntry(45000, 50.0, 70.0, FuelType.DIESEL, partial=False))
            tracker.add_entry(FuelEntry(45500, 50.0, 70.0, FuelType.DIESEL, partial=False))
            
            # Second trip: 500km on 48L of HVO100
            tracker.add_entry(FuelEntry(45500, 48.0, 72.0, FuelType.HVO100, partial=False))
            tracker.add_entry(FuelEntry(46000, 48.0, 72.0, FuelType.HVO100, partial=False))
            
            # Get summary
            summary = stats.get_statistics_summary(current_odometer=46000)
            
            assert summary["total_entries"] == 4
            assert summary["full_refuels"] == 4
            assert summary["average_consumption_l_per_100km"] is not None
            assert summary["consumption_by_fuel_type"]["Diesel"] == pytest.approx(10.0, rel=0.01)
            assert summary["consumption_by_fuel_type"]["HVO100"] == pytest.approx(9.6, rel=0.01)


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v"])
