"""Fuel consumption statistics module for miqro_can service."""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from fuel_tracker import FuelTracker, FuelEntry, FuelType


class FuelStatistics:
    """Calculates fuel consumption statistics."""
    
    def __init__(self, tracker: FuelTracker):
        """Initialize fuel statistics calculator.
        
        Args:
            tracker: FuelTracker instance with fuel data
        """
        self.tracker = tracker
    
    def get_average_consumption(self, liters_per_100km: bool = True) -> Optional[float]:
        """Calculate average fuel consumption across all full refuels.
        
        Args:
            liters_per_100km: If True, return l/100km; if False, return km/liter
            
        Returns:
            Average consumption or None if insufficient data
        """
        entries = self.tracker.get_full_refuels_only()
        if len(entries) < 2:
            return None
        
        total_liters = sum(e.liters for e in entries)
        total_distance = entries[-1].odometer - entries[0].odometer
        
        if total_distance == 0:
            return None
        
        consumption = (total_liters / total_distance) * 100
        
        if liters_per_100km:
            return consumption
        else:
            return 100 / consumption if consumption > 0 else None
    
    def get_consumption_by_fuel_type(self, liters_per_100km: bool = True) -> Dict[str, Optional[float]]:
        """Calculate average consumption for each fuel type.
        
        Args:
            liters_per_100km: If True, return l/100km; if False, return km/liter
            
        Returns:
            Dictionary mapping fuel type names to consumption values
        """
        results = {}
        
        for fuel_type in FuelType:
            entries = self.tracker.get_entries_by_type(fuel_type)
            entries = [e for e in entries if not e.partial]
            
            if len(entries) < 2:
                results[fuel_type.value] = None
                continue
            
            total_liters = sum(e.liters for e in entries)
            total_distance = entries[-1].odometer - entries[0].odometer
            
            if total_distance == 0:
                results[fuel_type.value] = None
                continue
            
            consumption = (total_liters / total_distance) * 100
            
            if liters_per_100km:
                results[fuel_type.value] = consumption
            else:
                results[fuel_type.value] = 100 / consumption if consumption > 0 else None
        
        return results
    
    def get_last_leg_consumption(self, liters_per_100km: bool = True) -> Optional[float]:
        """Calculate fuel consumption since the last full refuel.
        
        Args:
            liters_per_100km: If True, return l/100km; if False, return km/liter
            
        Returns:
            Consumption on last leg or None if insufficient data
        """
        entries = self.tracker.get_full_refuels_only()
        if len(entries) < 2:
            return None
        
        last_full_refuel = entries[-1]
        liters_since = sum(e.liters for e in entries if e.timestamp > last_full_refuel.timestamp)
        
        # Get current odometer - for this we need access to current odometer
        # This will be passed in from the service
        # For now, return None as we need current state
        return None
    
    def get_last_leg_consumption_with_odometer(
        self, current_odometer: float, liters_per_100km: bool = True
    ) -> Optional[float]:
        """Calculate fuel consumption since the last full refuel.
        
        Args:
            current_odometer: Current odometer reading in km
            liters_per_100km: If True, return l/100km; if False, return km/liter
            
        Returns:
            Consumption on last leg or None if insufficient data
        """
        entries = self.tracker.get_full_refuels_only()
        if len(entries) < 1:
            return None
        
        last_full_refuel = entries[-1]
        distance_since = current_odometer - last_full_refuel.odometer
        
        if distance_since <= 0:
            return None
        
        liters_since = sum(
            e.liters for e in self.tracker.get_entries()
            if e.timestamp > last_full_refuel.timestamp and not e.partial
        )
        
        consumption = (liters_since / distance_since) * 100
        
        if liters_per_100km:
            return consumption
        else:
            return 100 / consumption if consumption > 0 else None
    
    def get_cost_per_liter_by_type(self) -> Dict[str, Optional[float]]:
        """Calculate average cost per liter for each fuel type.
        
        Returns:
            Dictionary mapping fuel type names to cost per liter
        """
        results = {}
        
        for fuel_type in FuelType:
            entries = self.tracker.get_entries_by_type(fuel_type)
            
            if not entries:
                results[fuel_type.value] = None
                continue
            
            total_cost = sum(e.cost for e in entries)
            total_liters = sum(e.liters for e in entries)
            
            if total_liters == 0:
                results[fuel_type.value] = None
                continue
            
            results[fuel_type.value] = total_cost / total_liters
        
        return results
    
    def get_cost_per_km_by_type(self) -> Dict[str, Optional[float]]:
        """Calculate average cost per km for each fuel type.
        
        Returns:
            Dictionary mapping fuel type names to cost per km
        """
        results = {}
        
        for fuel_type in FuelType:
            entries = self.tracker.get_entries_by_type(fuel_type)
            entries = [e for e in entries if not e.partial]
            
            if len(entries) < 2:
                results[fuel_type.value] = None
                continue
            
            total_cost = sum(e.cost for e in entries)
            total_distance = entries[-1].odometer - entries[0].odometer
            
            if total_distance == 0:
                results[fuel_type.value] = None
                continue
            
            results[fuel_type.value] = total_cost / total_distance
        
        return results
    
    def get_statistics_summary(self, current_odometer: float) -> Dict[str, Any]:
        """Get a comprehensive summary of all statistics.
        
        Args:
            current_odometer: Current odometer reading in km
            
        Returns:
            Dictionary with all statistics
        """
        return {
            "average_consumption_l_per_100km": self.get_average_consumption(liters_per_100km=True),
            "average_consumption_km_per_liter": self.get_average_consumption(liters_per_100km=False),
            "consumption_by_fuel_type": self.get_consumption_by_fuel_type(liters_per_100km=True),
            "last_leg_consumption": self.get_last_leg_consumption_with_odometer(
                current_odometer, liters_per_100km=True
            ),
            "cost_per_liter_by_type": self.get_cost_per_liter_by_type(),
            "cost_per_km_by_type": self.get_cost_per_km_by_type(),
            "total_entries": len(self.tracker.get_entries()),
            "full_refuels": len(self.tracker.get_full_refuels_only()),
        }
