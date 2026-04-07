"""
POI Types Registry

Centralized configuration for all POI types using Pydantic models.
Provides type safety, validation, and sensible defaults.
"""

from pydantic import BaseModel, Field


class MatchingRules(BaseModel):
    """Type-specific matching parameters."""

    buffer_distance: float = Field(
        default=100,
        description="Search radius in meters",
        gt=0,
    )
    similarity_threshold: float = Field(
        default=0.6,
        description="Name similarity threshold (0-1)",
        ge=0,
        le=1,
    )


class OvertureConfig(BaseModel):
    """Overture data configuration for a POI type."""

    level_2_categories: list[str] = Field(
        description="Overture level 2 categories to fetch"
    )
    confidence_threshold: float = Field(
        default=0.5,
        description="Minimum confidence score for Overture features",
        ge=0,
        le=1,
    )


class OSMConfig(BaseModel):
    """OSM tag configuration for a POI type."""

    conditions: list[dict[str, str]] = Field(
        description=(
            "List of tag conditions. Each condition is a dict of {key: value} pairs "
            "where ALL tags must be present. A feature matches if ANY condition is satisfied."
        )
    )


class POITypeConfig(BaseModel):
    """Complete configuration for a POI type."""

    overture: OvertureConfig
    osm: OSMConfig
    matching: MatchingRules = Field(default_factory=MatchingRules)


# POI type registry
POI_TYPES: dict[str, POITypeConfig] = {
    "restaurant": POITypeConfig(
        overture=OvertureConfig(
            level_2_categories=["restaurant", "bar", "cafe"],
        ),
        osm=OSMConfig(
            conditions=[
                {"amenity": "restaurant"},
                {"amenity": "bar"},
                {"amenity": "pub"},
                {"amenity": "fast_food"},
                {"amenity": "cafe"},
            ]
        ),
        matching=MatchingRules(
            buffer_distance=100,
            similarity_threshold=0.6,
        ),
    ),
    "nail_salon": POITypeConfig(
        overture=OvertureConfig(
            level_2_categories=["nail_salon"],
        ),
        osm=OSMConfig(
            conditions=[
                {"shop": "beauty", "beauty": "nails"},
            ]
        ),
        matching=MatchingRules(
            buffer_distance=50,
            similarity_threshold=0.65,
        ),
    ),
    "hotel": POITypeConfig(
        overture=OvertureConfig(
            level_2_categories=["hotel", "motel", "inn", "lodge", "hostel"],
        ),
        osm=OSMConfig(
            conditions=[
                {"tourism": "hotel"},
                {"tourism": "motel"},
                {"tourism": "hostel"},
            ]
        ),
        matching=MatchingRules(
            buffer_distance=100,
            similarity_threshold=0.6,
        ),
    ),
}


def get_poi_type(poi_type_name: str) -> POITypeConfig | None:
    """
    Get the configuration for a specific POI type.

    Args:
        poi_type_name: Name of the POI type (e.g., 'restaurant', 'nail_salon')

    Returns:
        POITypeConfig with POI type configuration, or None if not found
    """
    return POI_TYPES.get(poi_type_name)


def get_all_poi_types() -> list[str]:
    """
    Get list of all registered POI types.

    Returns:
        List of POI type names
    """
    return list(POI_TYPES.keys())


def get_overture_categories(poi_type_name: str) -> list[str]:
    """
    Get Overture level 2 categories for a POI type.

    Args:
        poi_type_name: Name of the POI type

    Returns:
        List of Overture category codes, empty list if POI type not found
    """
    poi_type = get_poi_type(poi_type_name)
    if poi_type:
        return poi_type.overture.level_2_categories
    return []


def get_osm_conditions(poi_type_name: str) -> list[dict[str, str]]:
    """
    Get OSM tag conditions that identify a POI type.

    Each condition is a dictionary of {tag_key: tag_value} pairs.
    All tags in a condition must be present for it to match.
    A feature matches the POI type if it satisfies ANY of the conditions.

    Args:
        poi_type_name: Name of the POI type

    Returns:
        List of condition dictionaries, empty list if POI type not found
    """
    poi_type = get_poi_type(poi_type_name)
    if poi_type:
        return poi_type.osm.conditions
    return []


def matches_osm_tags(tags: dict, poi_type_name: str) -> bool:
    """
    Check if a set of OSM tags matches the given POI type.

    Args:
        tags: Dictionary of OSM tags {key: value}
        poi_type_name: Name of the POI type to check against

    Returns:
        True if tags match this POI type, False otherwise
    """
    conditions = get_osm_conditions(poi_type_name)
    if not conditions:
        return False

    # Check if tags match any of the conditions for this POI type
    # A condition matches if all its tags are present with the correct values
    for condition in conditions:
        if all(tags.get(key) == value for key, value in condition.items()):
            return True

    return False


def get_matching_rules(poi_type_name: str) -> MatchingRules | None:
    """
    Get type-specific matching rules (buffer distance, similarity threshold, etc).

    Args:
        poi_type_name: Name of the POI type

    Returns:
        MatchingRules with matching parameters, or None if POI type not found
    """
    poi_type = get_poi_type(poi_type_name)
    if poi_type:
        return poi_type.matching
    return None
