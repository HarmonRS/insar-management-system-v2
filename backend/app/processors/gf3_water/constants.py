"""Class ids and product labels used by GF-3 water extraction."""

CLASS_NON_WATER = 0
CLASS_HIGH_CONFIDENCE_WATER = 1
CLASS_KNOWN_WATER = 2
CLASS_PADDY_WATER_LIKE = 3
CLASS_LOW_CONFIDENCE_WATER = 4
CLASS_CARTOGRAPHIC_WATER = 5
CLASS_INVALID = 255

CLASS_NAMES = {
    CLASS_NON_WATER: "non_water",
    CLASS_HIGH_CONFIDENCE_WATER: "high_confidence_water",
    CLASS_KNOWN_WATER: "known_river_lake_water",
    CLASS_PADDY_WATER_LIKE: "paddy_water_like",
    CLASS_LOW_CONFIDENCE_WATER: "low_confidence_water",
    CLASS_CARTOGRAPHIC_WATER: "cartographic_water",
    CLASS_INVALID: "invalid",
}

