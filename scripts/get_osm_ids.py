"""
Script to fetch OpenStreetMap data from QLever API.

This script dynamically generates SPARQL queries based on configured POI types,
allowing easy addition of new POI types without modifying the query logic.
"""

import datetime
import json
import sys

import requests

from .poi_types import get_all_poi_types, get_osm_conditions

# Configuration
QLEVER_ENDPOINT = "https://qlever.dev/api/osm-planet"
# relation = 162069  # DC
relation = 148838  # US


def build_sparql_query(relation_id: int) -> str:
    """
    Build a SPARQL query dynamically from configured POI types.

    Args:
        relation_id: OSM relation ID to query within

    Returns:
        SPARQL query string
    """
    # Collect all tag conditions from all POI types
    all_conditions = []

    for poi_type in get_all_poi_types():
        conditions = get_osm_conditions(poi_type)
        all_conditions.extend(conditions)

    # Build the tag filter - each condition becomes a separate query branch
    # A feature matches if it satisfies ANY condition
    tag_filters = []

    for condition in all_conditions:
        # Each condition is a dict of {key: value} pairs that must ALL be present
        filter_parts = []
        for key, value in condition.items():
            filter_parts.append(f'?id osmkey:{key} "{value}" .')

        # Join all parts for this condition
        if filter_parts:
            tag_filters.append(" ".join(filter_parts))

    # Combine all conditions with UNION
    if not tag_filters:
        raise ValueError("No POI type conditions found in registry")

    # Build the UNION query - if only one condition, no UNION needed
    if len(tag_filters) == 1:
        tag_filter_str = tag_filters[0]
    else:
        tag_filter_str = "{ " + " } UNION { ".join(tag_filters) + " }"

    query = f"""
PREFIX osmkey: <https://www.openstreetmap.org/wiki/Key:>
PREFIX osmrel: <https://www.openstreetmap.org/relation/>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX osm: <https://www.openstreetmap.org/>
PREFIX ogc: <http://www.opengis.net/rdf#>
PREFIX geo: <http://www.opengis.net/ont/geosparql#>
PREFIX geof: <http://www.opengis.net/def/function/geosparql/>
SELECT ?id ?name ?housenumber ?centroid WHERE {{
  osmrel:{relation_id} ogc:sfIntersects ?id .
  {tag_filter_str}
  ?id osmkey:name ?name .
  FILTER NOT EXISTS {{ ?id osmkey:brand:wikidata ?wikidata . }}
  OPTIONAL {{ ?id osmkey:addr:housenumber ?housenumber . }}
  ?id geo:hasGeometry/geo:asWKT ?geometry .
  BIND(geof:centroid(?geometry) AS ?centroid)
}}"""

    return query


def fetch_osm_data(query_string: str) -> list:
    """
    Fetch data from QLever API.

    Args:
        query_string: SPARQL query string

    Returns:
        List of results, where each result is [id, name, geometry]
    """
    try:
        # Prepare the request
        params = {"query": query_string}

        print(f"Fetching data from {QLEVER_ENDPOINT}...", file=sys.stderr)

        # Make the request
        response = requests.get(QLEVER_ENDPOINT, params=params)
        response.raise_for_status()

        # Parse JSON response
        data = response.json()

        results = data["results"]["bindings"]

        print(f"Fetched {len(results)} results", file=sys.stderr)

        return results

    except requests.exceptions.RequestException as e:
        print(f"Error fetching data: {e}", file=sys.stderr)
        return []
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON response: {e}", file=sys.stderr)
        return []


def main():
    """Main function to fetch and display OSM data."""
    try:
        # Build query from POI types registry
        print(
            f"Building SPARQL query from {len(get_all_poi_types())} POI types...",
            file=sys.stderr,
        )
        print(f"POI types: {', '.join(get_all_poi_types())}", file=sys.stderr)

        query = build_sparql_query(relation)
        print(
            f"Generated SPARQL query with {len(get_all_poi_types())} POI type conditions",
            file=sys.stderr,
        )
    except ValueError as e:
        print(f"Error building query: {e}", file=sys.stderr)
        return

    results = fetch_osm_data(query)

    if not results:
        print("No results found or error occurred", file=sys.stderr)
        return

    # Process and display results
    print(f"\nFound {len(results)} amenities:\n", file=sys.stderr)

    output = []
    for result in results:
        if len(result) >= 3:
            osm_id = (
                result.get("id")
                .get("value")
                .removeprefix("https://www.openstreetmap.org/")
            )
            name = result.get("name").get("value")
            housenumber = result.get("housenumber", {"value": None}).get("value")
            geometry = result.get("centroid").get("value")
            object = {
                "type": "Feature",
                "properties": {
                    "@id": osm_id,
                    "name": name,
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [
                        float(x)
                        for x in geometry.removeprefix("POINT(")
                        .removesuffix(")")
                        .split(" ")
                    ],
                },
            }
            if housenumber:
                object["properties"]["addr:housenumber"] = housenumber
            output.append(object)

        else:
            print(f"Warning: Unexpected result format: {result}", file=sys.stderr)

    # Optionally save to file
    output_file = "data/osm_qlever.geojson"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "type": "FeatureCollection",
                "timestamp": str(datetime.datetime.now()),
                "features": output,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(f"\nResults saved to {output_file}", file=sys.stderr)


if __name__ == "__main__":
    main()
