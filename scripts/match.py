import json
import logging
import re
import time
from datetime import datetime

import atlus
import geopandas as gpd
import numpy as np
import overturetoosm
import pandas as pd
from rapidfuzz import fuzz
from rtree import index
from tqdm import tqdm

from .poi_types import get_matching_rules

start = datetime.now()


def lowercase_url(url: str) -> str:
    """
    Use splitting and lowercase to convert a URL to lowercase.
    """
    # Split by '//' to separate protocol from the rest
    if "//" in url:
        protocol, rest = url.split("//", 1)
        protocol += "//"
    else:
        protocol, rest = "", url

    # Split domain from path and lowercase the domain
    domain, _, path = rest.partition("/")

    return protocol + domain.lower() + ("/" + path if path else "")


TRACKING_PARAMS_PATTERN = [
    (r"utm_[^&=]*"),  # All UTM parameters
    (r"[a-z_]*(?:id|token|source|ref)"),  # catchall
    (r"_ga"),  # Google Analytics
    (r"hsCtaTracking"),  # HubSpot
    (r"hsa_[^&=]*"),  # HubSpot ads
    (r"_hs[^&=]*"),  # HubSpot
    (r"ref_?"),  # Generic ref parameters
    (r"lipi"),  # LinkedIn
]

TRACKING_PARAMS_REGEX = re.compile(
    r"&?(?:" + "|".join(TRACKING_PARAMS_PATTERN) + r")(=[^&=]+)"
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(f"logs/match_{start.strftime('%Y%m%d_%H%M%S')}.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def remove_tracking_params_regex(url: str) -> str:
    """
    Remove tracking parameters from a URL using regex approach.

    This is a more aggressive regex-based approach that removes common
    tracking parameter patterns. It's faster but less precise than the
    parsing approach.

    Args:
        url: The URL to clean

    Returns:
        The cleaned URL without tracking parameters
    """
    if not url:
        return url

    # Remove all tracking parameters in a single pass
    cleaned_url = TRACKING_PARAMS_REGEX.sub("", url)

    # Replace & at the start of query string with ?
    cleaned_url = re.sub(r"\?&", "?", cleaned_url.rstrip("&?"))

    return cleaned_url


def load_and_prepare_data(osm_layer_path: str, overture_layer_path: str) -> tuple:
    """Load data and prepare for matching"""
    logger.info(f"Loading OSM layer from: {osm_layer_path}")
    try:
        osm_layer = gpd.read_file(osm_layer_path)
        logger.info(f"Successfully loaded OSM layer with {len(osm_layer):,} features")
    except Exception as e:
        logger.error(f"Failed to load OSM layer: {e}")
        raise

    logger.info(f"Loading Overture layer from: {overture_layer_path}")
    try:
        with open(overture_layer_path) as f:
            overture_json = json.load(f)
        logger.info("Successfully loaded Overture JSON")
    except Exception as e:
        logger.error(f"Failed to load Overture layer: {e}")
        raise

    overture_layer = gpd.GeoDataFrame.from_features(
        overture_json["features"], crs="EPSG:4326"
    )

    # Store original 4326 coordinates to avoid repeated CRS transformations
    overture_layer["lon"] = overture_layer.geometry.x
    overture_layer["lat"] = overture_layer.geometry.y

    logger.info("Converting to projected CRS (EPSG:3857) for distance calculations")
    try:
        # Use a projected CRS for accurate distance calculations
        # Choose appropriate UTM zone or use a local projection
        osm_layer = osm_layer.to_crs("EPSG:3857")  # Web Mercator (meters)
        overture_layer = overture_layer.to_crs("EPSG:3857")
        logger.info("CRS conversion successful")
    except Exception as e:
        logger.error(f"Failed to convert CRS: {e}")
        raise

    logger.info(f"OSM layer has {len(osm_layer):,} features")
    logger.info(f"Overture layer has {len(overture_layer):,} features")

    return osm_layer, overture_layer


def build_spatial_index(layer: gpd.GeoDataFrame) -> index.Index:
    """Build spatial index for the layer"""
    logger.info("Building spatial index...")
    try:
        spatial_idx = index.Index()
        for idx, geom in enumerate(layer.geometry):
            spatial_idx.insert(idx, geom.bounds)
        logger.info(f"Spatial index built with {len(layer):,} features")
        return spatial_idx
    except Exception as e:
        logger.error(f"Failed to build spatial index: {e}")
        raise


def preprocess_overture_layer(overture_layer: gpd.GeoDataFrame) -> dict:
    """Pre-extract frequently accessed data for faster lookup"""
    logger.info("Preprocessing Overture layer for faster access...")

    preprocessed: dict = {
        "names": [],
        "ids": [],
        "lon": [],
        "lat": [],
        "geometries": [],
    }

    # Extract data by position to match spatial index
    for i in range(len(overture_layer)):
        row = overture_layer.iloc[i]

        # Extract name
        names_dict = row.get("names")
        if names_dict and isinstance(names_dict, dict):
            name = names_dict.get("primary", "")
        else:
            name = ""

        preprocessed["names"].append(name)
        preprocessed["ids"].append(row["id"])
        preprocessed["lon"].append(row["lon"])
        preprocessed["lat"].append(row["lat"])
        preprocessed["geometries"].append(row.geometry)

    # Convert to numpy for faster indexing
    preprocessed["names"] = np.array(preprocessed["names"], dtype=object)  # type: ignore
    preprocessed["ids"] = np.array(preprocessed["ids"], dtype=object)  # type: ignore
    preprocessed["lon"] = np.array(preprocessed["lon"], dtype=float)  # type: ignore
    preprocessed["lat"] = np.array(preprocessed["lat"], dtype=float)  # type: ignore

    logger.info("Preprocessing complete")
    return preprocessed


def find_matches_for_point(
    row_data: pd.Series,
    overture_layer: gpd.GeoDataFrame,
    preprocessed: dict,
    spatial_idx: index.Index,
    poi_type: str = "restaurant",
    buffer_distance: float = 100,
    similarity_threshold: float = 0.6,
) -> list[dict]:
    """
    Find matches for a single point - optimized version.

    Args:
        row_data: OSM feature data (from iterrows)
        overture_layer: GeoDataFrame of Overture features
        preprocessed: Preprocessed Overture data for fast access
        spatial_idx: Spatial index for Overture features
        poi_type: POI type name (e.g., 'restaurant', 'nail_salon')
        buffer_distance: Search radius in meters
        similarity_threshold: Name similarity threshold (0-1)
    """
    matches = []
    row_prep = row_data[1]
    row: dict = {k: v for k, v in dict(row_prep).items() if v and v is not None}

    point = row.get("geometry")
    osm_name = row.get("name", "")

    # Skip if no name
    if not osm_name or (isinstance(osm_name, float) and pd.isna(osm_name)):
        return matches

    # Ensure osm_name is a string for type safety
    osm_name = str(osm_name)

    # Buffer in meters (since we're using EPSG:3857)
    if point is None:
        return matches
    bounds = point.buffer(buffer_distance).bounds

    # Get candidate indices from spatial index
    candidate_indices = list(spatial_idx.intersection(bounds))

    if not candidate_indices:
        return matches

    # Calculate distances using preprocessed geometries
    candidate_geoms = [preprocessed["geometries"][i] for i in candidate_indices]
    distances = np.array([geom.distance(point) for geom in candidate_geoms])

    # Filter by distance first
    within_distance_mask = distances <= buffer_distance
    close_indices = np.array(candidate_indices)[within_distance_mask]
    close_distances = distances[within_distance_mask]

    if len(close_indices) == 0:
        return matches

    # Get pre-extracted names for close candidates
    close_names = preprocessed["names"][close_indices]

    # Now check string similarity only for close candidates
    for idx, distance, candidate_name in zip(
        close_indices, close_distances, close_names
    ):
        if not candidate_name:
            continue

        similarity = fuzz.ratio(osm_name, candidate_name) / 100.0

        if similarity >= similarity_threshold:
            # Use pre-stored lat/lon
            lon = preprocessed["lon"][idx]
            lat = preprocessed["lat"][idx]

            # Get full candidate row only when needed
            candidate = overture_layer.iloc[idx]

            # Build dict for processing without copying entire Series
            candidate_dict = {}
            for key in candidate.index:
                if key not in [
                    "basic_category",
                    "geometry",
                    "filename",
                    "operating_status",
                    "lon",
                    "lat",
                ]:
                    val = candidate[key]
                    if val is not None and not (
                        isinstance(val, float) and pd.isna(val)
                    ):
                        candidate_dict[key] = val

            if "names" in candidate_dict:
                candidate_dict["names"]["rules"] = None
            if "brand" in candidate_dict and "names" in candidate_dict.get("brand", {}):
                candidate_dict["brand"]["names"]["rules"] = None
                if candidate_dict["brand"]["names"].get("primary") is None:
                    del candidate_dict["brand"]

            # Bad data handling
            # basic_category missing from data
            if "basic_category" not in candidate_dict:
                candidate_dict["basic_category"] = "unknown"
            # update_time doesn't conform to regex
            if "sources" in candidate_dict:
                for source in candidate_dict["sources"]:
                    update_time = source.get("update_time", "")
                    if "00:00:00.000" in update_time:
                        update_time = update_time.replace("00.000", "00Z")
                    while update_time.endswith("ZZ"):
                        update_time = update_time[:-1]
                    source["update_time"] = update_time

            candidate_tags = overturetoosm.process_place(candidate_dict)

            try:
                street_addr = candidate_tags.get("addr:full", "")
                if street_addr:
                    address_tags = atlus.get_address(street_addr)[0]
                    candidate_tags.update(address_tags)
            except ValueError as e:
                logger.debug(
                    f"Address parsing failed for {candidate_tags.get('addr:full', '')}: {e}"
                )
            except Exception as e:
                logger.warning(f"Unexpected error in address parsing: {e}")

            if "addr:housenumber" in candidate_tags and "addr:housenumber" in row:
                if candidate_tags["addr:housenumber"] != row["addr:housenumber"]:
                    continue

            try:
                phone = candidate_tags.get("phone", "")
                if phone:
                    phone_tag = atlus.get_phone(phone)
                    candidate_tags.update({"phone": phone_tag})
            except ValueError as e:
                logger.debug(
                    f"Phone parsing failed for {candidate_tags.get('phone', '')}: {e}"
                )
            except Exception as e:
                logger.warning(f"Unexpected error in phone parsing: {e}")

            if "website" in candidate_tags:
                if any(
                    keyword in candidate_tags["website"]
                    for keyword in [
                        # order
                        "ubereats.com",
                        "doordash.com",
                        "grubhub.com",
                        # reservation
                        "opentable.com",
                        "resy.com",
                        # maps
                        "google.com",
                        "g.page",
                        "apple.com",
                        "yelp.com",
                        "groupon.com",
                        "eventbrite.com",
                        "musthavemenus.com",
                        "parkopedia.com",
                        # POS
                        "toasttab.com",
                        "dineblast.com",
                        "thanx.com",
                        "order.online",
                        "digitalpour.com",
                        "waitrapp.com",
                        "culinarycloud.co",
                        # misc
                        "bit.ly",
                        "business.site",
                        "spotify.com",
                        "facebook.com",
                        "instagram.com",
                        "twitter.com",
                        "x.com",
                        "whitepages.com",
                        "yellowpages.com",
                        "yahoo.com",
                        "mapquest.com",
                        "glassdoor.com",
                        "restaurant.com",
                        "cortera.com",
                        "finduslocal.com",
                        "redfin.com",
                        "dandb.com",
                        "chamberofcommerce.com",
                        "wikidot.com",
                        "...",
                        '"',
                    ]
                ):
                    candidate_tags.pop("website")
                elif "website" in candidate_tags and candidate_tags["website"]:
                    website_tag = lowercase_url(
                        remove_tracking_params_regex(candidate_tags["website"])
                        .replace("?&", "?")
                        .rstrip("?& ")
                    )
                    if re.match(
                        r"(?i)\b((?:https?:(?:/{1,3}|[a-z0-9%])|[a-z0-9.\-]+[.](?:com|net|org|edu|gov|mil|aero|asia|biz|cat|coop|info|int|jobs|mobi|museum|name|post|pro|tel|travel|xxx|ac|ad|ae|af|ag|ai|al|am|an|ao|aq|ar|as|at|au|aw|ax|az|ba|bb|bd|be|bf|bg|bh|bi|bj|bm|bn|bo|br|bs|bt|bv|bw|by|bz|ca|cc|cd|cf|cg|ch|ci|ck|cl|cm|cn|co|cr|cs|cu|cv|cx|cy|cz|dd|de|dj|dk|dm|do|dz|ec|ee|eg|eh|er|es|et|eu|fi|fj|fk|fm|fo|fr|ga|gb|gd|ge|gf|gg|gh|gi|gl|gm|gn|gp|gq|gr|gs|gt|gu|gw|gy|hk|hm|hn|hr|ht|hu|id|ie|il|im|in|io|iq|ir|is|it|je|jm|jo|jp|ke|kg|kh|ki|km|kn|kp|kr|kw|ky|kz|la|lb|lc|li|lk|lr|ls|lt|lu|lv|ly|ma|mc|md|me|mg|mh|mk|ml|mm|mn|mo|mp|mq|mr|ms|mt|mu|mv|mw|mx|my|mz|na|nc|ne|nf|ng|ni|nl|no|np|nr|nu|nz|om|pa|pe|pf|pg|ph|pk|pl|pm|pn|pr|ps|pt|pw|py|qa|re|ro|rs|ru|rw|sa|sb|sc|sd|se|sg|sh|si|sj|Ja|sk|sl|sm|sn|so|sr|ss|st|su|sv|sx|sy|sz|tc|td|tf|tg|th|tj|tk|tl|tm|tn|to|tp|tr|tt|tv|tw|tz|ua|ug|uk|us|uy|uz|va|vc|ve|vg|vi|vn|vu|wf|ws|ye|yt|yu|za|zm|zw)/)(?:[^\s()<>{}\[\]]+|\([^\s()]*?\([^\s()]+\)[^\s()]*?\)|\([^\s]+?\))+(?:\([^\s()]*?\([^\s()]+\)[^\s()]*?\)|\([^\s]+?\)|[^\s`!()\[\]{};:'\".,<>?«»“”‘’])|(?:(?<!@)[a-z0-9]+(?:[.\-][a-z0-9]+)*[.](?:com|net|org|edu|gov|mil|aero|asia|biz|cat|coop|info|int|jobs|mobi|museum|name|post|pro|tel|travel|xxx|ac|ad|ae|af|ag|ai|al|am|an|ao|aq|ar|as|at|au|aw|ax|az|ba|bb|bd|be|bf|bg|bh|bi|bj|bm|bn|bo|br|bs|bt|bv|bw|by|bz|ca|cc|cd|cf|cg|ch|ci|ck|cl|cm|cn|co|cr|cs|cu|cv|cx|cy|cz|dd|de|dj|dk|dm|do|dz|ec|ee|eg|eh|er|es|et|eu|fi|fj|fk|fm|fo|fr|ga|gb|gd|ge|gf|gg|gh|gi|gl|gm|gn|gp|gq|gr|gs|gt|gu|gw|gy|hk|hm|hn|hr|ht|hu|id|ie|il|im|in|io|iq|ir|is|it|je|jm|jo|jp|ke|kg|kh|ki|km|kn|kp|kr|kw|ky|kz|la|lb|lc|li|lk|lr|ls|lt|lu|lv|ly|ma|mc|md|me|mg|mh|mk|ml|mm|mn|mo|mp|mq|mr|ms|mt|mu|mv|mw|mx|my|mz|na|nc|ne|nf|ng|ni|nl|no|np|nr|nu|nz|om|pa|pe|pf|pg|ph|pk|pl|pm|pn|pr|ps|pt|pw|py|qa|re|ro|rs|ru|rw|sa|sb|sc|sd|se|sg|sh|si|sj|Ja|sk|sl|sm|sn|so|sr|ss|st|su|sv|sx|sy|sz|tc|td|tf|tg|th|tj|tk|tl|tm|tn|to|tp|tr|tt|tv|tw|tz|ua|ug|uk|us|uy|uz|va|vc|ve|vg|vi|vn|vu|wf|ws|ye|yt|yu|za|zm|zw)\b/?(?!@)))",
                        website_tag,
                    ):
                        candidate_tags["website"] = website_tag

            # Remove toll-free numbers
            if "phone" in candidate_tags and any(
                toll_free in candidate_tags["phone"]
                for toll_free in [
                    "+1-800",
                    "+1-888",
                    "+1-877",
                    "+1-866",
                    "+1-877",
                    "+1-855",
                    "+1-844",
                    "+1-833",
                ]
            ):
                candidate_tags.pop("phone")

            for toss_tag in ["addr:country", "addr:full", "source"]:
                candidate_tags.pop(toss_tag, None)

            matches.append(
                {
                    "osm_id": row["@id"],
                    "overture_id": preprocessed["ids"][idx],
                    "lon": float(lon),
                    "lat": float(lat),
                    "distance_m": round(float(distance), 1),
                    "similarity": similarity,
                    "overture_tags": candidate_tags,
                }
            )

    return matches


def process_chunk(chunk_data, overture_layer, preprocessed, spatial_idx):
    """Process a chunk of OSM features"""
    all_matches = []
    for row_data in chunk_data:
        matches = find_matches_for_point(
            row_data, overture_layer, preprocessed, spatial_idx
        )
        all_matches.extend(matches)
    return all_matches


def main():
    start_time = time.time()
    logger.info("=" * 60)
    logger.info("Starting match.py script")
    logger.info("=" * 60)

    try:
        # Load and prepare data
        load_start = time.time()
        osm_layer, overture_layer = load_and_prepare_data(
            "data/osm_qlever.geojson", "data/overture.geojson"
        )
        load_duration = time.time() - load_start
        logger.info(f"Data loading completed in {load_duration:.2f} seconds")

        # Build spatial index
        index_start = time.time()
        spatial_idx = build_spatial_index(overture_layer)
        index_duration = time.time() - index_start
        logger.info(f"Spatial index built in {index_duration:.2f} seconds")

        # Preprocess overture layer for faster access
        preprocess_start = time.time()
        preprocessed = preprocess_overture_layer(overture_layer)
        preprocess_duration = time.time() - preprocess_start
        logger.info(f"Preprocessing completed in {preprocess_duration:.2f} seconds")

        # Prepare row data for processing
        row_data = list(osm_layer.iterrows())
        logger.info(f"Prepared {len(row_data)} rows for processing")

        # Get POI type (currently hardcoded to restaurant, can be made configurable)
        poi_type = "restaurant"
        matching_rules = get_matching_rules(poi_type)
        if not matching_rules:
            logger.error(f"No matching rules found for POI type: {poi_type}")
            raise ValueError(f"POI type '{poi_type}' not configured")

        buffer_distance = matching_rules.buffer_distance
        similarity_threshold = matching_rules.similarity_threshold
        logger.info(f"Using POI type: {poi_type}")
        logger.info(f"  Buffer distance: {buffer_distance}m")
        logger.info(f"  Similarity threshold: {similarity_threshold}")

        # Option 1: Sequential with progress bar (easier to debug)
        logger.info("Starting match processing...")
        matching_start = time.time()
        all_matches = []
        error_count = 0

        for data in tqdm(row_data, total=len(osm_layer), desc="Processing matches"):
            try:
                matches = find_matches_for_point(
                    data,
                    overture_layer,
                    preprocessed,
                    spatial_idx,
                    poi_type=poi_type,
                    buffer_distance=buffer_distance,
                    similarity_threshold=similarity_threshold,
                )
                all_matches.extend(matches)
            except Exception as e:
                error_count += 1
                logger.error(f"Error processing row {data[0]}: {e}")
                if error_count > 100:
                    logger.critical("Too many errors encountered, stopping...")
                    raise

        matching_duration = time.time() - matching_start
        logger.info(f"Match processing completed in {matching_duration:.2f} seconds")
        if error_count > 0:
            logger.warning(f"Encountered {error_count} errors during processing")

        # Option 2: Parallel processing (uncomment to use)
        # from multiprocessing import Pool, cpu_count
        # from functools import partial
        # logger.info(f"Processing matches using {cpu_count()} cores...")
        # chunk_size = max(1, len(row_data) // (cpu_count() * 4))
        # chunks = [row_data[i:i + chunk_size] for i in range(0, len(row_data), chunk_size)]
        #
        # process_func = partial(process_chunk,
        #                       overture_layer=overture_layer,
        #                       preprocessed=preprocessed,
        #                       spatial_idx=spatial_idx,
        #                       poi_type=poi_type)
        #
        # with Pool(cpu_count()) as pool:
        #     results = list(tqdm(pool.imap(process_func, chunks), total=len(chunks)))
        #
        # all_matches = [match for chunk_matches in results for match in chunk_matches]

        # Save results
        logger.info(f"Found {len(all_matches):,} matches")
        save_start = time.time()
        matches_df = pd.DataFrame(all_matches)
        output_file = "data/matches.jsonl"
        matches_df.to_json(output_file, orient="records", lines=True)
        save_duration = time.time() - save_start
        logger.info(f"Results saved to {output_file} in {save_duration:.2f} seconds")

        # Summary
        total_duration = time.time() - start_time
        logger.info("=" * 60)
        logger.info("EXECUTION SUMMARY")
        logger.info("=" * 60)
        logger.info(
            f"Total execution time: {total_duration:.2f} seconds ({total_duration / 60:.2f} minutes)"
        )
        logger.info(f"  - Data loading: {load_duration:.2f}s")
        logger.info(f"  - Spatial indexing: {index_duration:.2f}s")
        logger.info(f"  - Preprocessing: {preprocess_duration:.2f}s")
        logger.info(f"  - Match processing: {matching_duration:.2f}s")
        logger.info(f"  - Saving results: {save_duration:.2f}s")
        logger.info(f"POI type: {poi_type}")
        logger.info(f"OSM features processed: {len(osm_layer):,}")
        logger.info(f"Overture features indexed: {len(overture_layer):,}")
        logger.info(f"Total matches found: {len(all_matches):,}")
        logger.info(
            f"Match rate: {len(all_matches) / len(osm_layer):.2f} matches per OSM feature"
        )
        logger.info("Script completed successfully")
        logger.info("=" * 60)

        # Log to file for later reference
        with open("logs/match_timing.log", "a") as f:
            f.write(
                " | ".join(
                    [
                        f"{start.strftime('%Y-%m-%d %H:%M:%S')}",
                        f"Total: {total_duration:.2f} sec ({total_duration / 60:.2f} min)",
                        f"Load: {load_duration:.2f} sec",
                        f"Index: {index_duration:.2f} sec",
                        f"Preprocess: {preprocess_duration:.2f} sec",
                        f"Match: {matching_duration:.2f} sec",
                        f"Save: {save_duration:.2f} sec",
                        f"OSM features: {len(osm_layer)}",
                        f"Overture features: {len(overture_layer)}",
                        f"Matches: {len(all_matches)}",
                        f"Match rate: {len(all_matches) / len(osm_layer):.2f}",
                    ]
                )
                + "\n"
            )

    except Exception as e:
        logger.critical(f"Script failed with error: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.warning("Script interrupted by user")
    except Exception as e:
        logger.critical(f"Script terminated with error: {e}")
        raise
