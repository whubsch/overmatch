"""
Script to load match data from JSONL file into DynamoDB.

This script reads the matches.jsonl file and stores the matches in DynamoDB
with the OSM ID as the partition key and an array of matches for each OSM element.
"""

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from tqdm import tqdm


def load_jsonl(file_path: str) -> list[dict]:
    """
    Load data from a JSONL file.

    Args:
        file_path: Path to the JSONL file

    Returns:
        List of dictionaries, one per line
    """
    matches = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                matches.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"Warning: Skipping invalid JSON on line {line_num}: {e}")
    return matches


def convert_floats_to_decimal(obj):
    """
    Recursively convert float values to Decimal for DynamoDB compatibility.

    Args:
        obj: Object to convert (dict, list, or primitive)

    Returns:
        Object with floats converted to Decimal
    """
    if isinstance(obj, list):
        return [convert_floats_to_decimal(item) for item in obj]
    elif isinstance(obj, dict):
        return {key: convert_floats_to_decimal(value) for key, value in obj.items()}
    elif isinstance(obj, float):
        return Decimal(str(obj))
    else:
        return obj


def group_matches_by_osm_id(matches: list[dict]) -> dict[str, list[dict]]:
    """
    Group matches by OSM ID and convert floats to Decimal during grouping.

    Args:
        matches: List of match dictionaries

    Returns:
        Dictionary mapping OSM ID to list of matches (already DynamoDB-ready)
    """
    grouped = defaultdict(list)

    for match in matches:
        osm_id = match.get("osm_id")
        if not osm_id:
            print(f"Warning: Skipping match without osm_id: {match}")
            continue

        # Convert floats to Decimal immediately during grouping (optimization)
        # This is faster than converting later at upload time
        match_data = {
            "overture_id": match.get("overture_id"),
            "lon": Decimal(str(match["lon"]))
            if "lon" in match and match["lon"] is not None
            else None,
            "lat": Decimal(str(match["lat"]))
            if "lat" in match and match["lat"] is not None
            else None,
            "distance_m": Decimal(str(match["distance_m"]))
            if "distance_m" in match and match["distance_m"] is not None
            else None,
            "similarity": Decimal(str(match["similarity"]))
            if "similarity" in match and match["similarity"] is not None
            else None,
            "overture_tags": convert_floats_to_decimal(match.get("overture_tags", {})),
        }
        grouped[osm_id].append(match_data)

    return grouped


def check_existing_items(
    osm_ids: list[str], table_name: str, region_name: str = "us-east-1"
) -> set[str]:
    """
    Check which OSM IDs already exist in DynamoDB to enable resumption.

    Args:
        osm_ids: List of OSM IDs to check
        table_name: Name of the DynamoDB table
        region_name: AWS region

    Returns:
        Set of OSM IDs that already exist in the table
    """
    dynamodb = boto3.resource("dynamodb", region_name=region_name)
    dynamodb.Table(table_name)
    existing = set()

    print("Checking for existing items (for resume capability)...")
    with tqdm(total=len(osm_ids), desc="Checking existing", unit="items") as pbar:
        # Check in batches of 100 (DynamoDB BatchGetItem limit)
        for i in range(0, len(osm_ids), 100):
            batch = osm_ids[i : i + 100]
            try:
                response = dynamodb.batch_get_item(
                    RequestItems={
                        table_name: {
                            "Keys": [{"element_id": osm_id} for osm_id in batch]
                        }
                    }
                )
                for item in response.get("Responses", {}).get(table_name, []):
                    existing.add(item["element_id"])
            except Exception as e:
                print(f"\nWarning: Could not check batch: {e}")
            pbar.update(len(batch))

    return existing


def upload_to_dynamodb(
    grouped_matches: dict[str, list[dict]],
    table_name: str,
    region_name: str = "us-east-1",
    batch_size: int = 25,
    skip_existing: bool = False,
) -> tuple[int, int, int]:
    """
    Upload grouped matches to DynamoDB with retry logic and resume capability.

    Args:
        grouped_matches: Dictionary mapping OSM ID to list of matches
        table_name: Name of the DynamoDB table
        region_name: AWS region
        batch_size: Number of items per batch (max 25 for DynamoDB)
        skip_existing: Whether to skip items that already exist

    Returns:
        Tuple of (successful_count, failed_count, skipped_count)
    """
    dynamodb = boto3.resource("dynamodb", region_name=region_name)
    table = dynamodb.Table(table_name)

    timestamp = datetime.now(timezone.utc).isoformat()
    successful = 0
    failed = 0
    skipped = 0

    osm_ids = list(grouped_matches.keys())

    # Check for existing items to enable resume
    existing_ids = set()
    if skip_existing:
        existing_ids = check_existing_items(osm_ids, table_name, region_name)
        if existing_ids:
            print(f"Found {len(existing_ids)} existing items, will skip them")
            skipped = len(existing_ids)
            osm_ids = [oid for oid in osm_ids if oid not in existing_ids]
            print(f"Will upload {len(osm_ids)} new items")
        print()

    # Process in batches with retry logic
    with tqdm(
        total=len(osm_ids), desc="Uploading to DynamoDB", unit="items", mininterval=0.5
    ) as pbar:
        for i in range(0, len(osm_ids), batch_size):
            batch = osm_ids[i : i + batch_size]
            max_retries = 3
            retry_count = 0

            while retry_count < max_retries:
                try:
                    # Refresh boto3 resource to pick up new credentials if refreshed
                    if retry_count > 0:
                        dynamodb = boto3.resource("dynamodb", region_name=region_name)
                        table = dynamodb.Table(table_name)

                    with table.batch_writer() as writer:
                        for osm_id in batch:
                            matches = grouped_matches[osm_id]

                            # Data is already converted to Decimal during grouping
                            item = {
                                "element_id": osm_id,
                                "matches": matches,
                                "match_count": len(matches),
                                "loaded_at": timestamp,
                            }

                            writer.put_item(Item=item)
                            successful += 1

                    pbar.update(len(batch))
                    break  # Success, exit retry loop

                except (ClientError, NoCredentialsError, RuntimeError) as e:
                    retry_count += 1
                    error_msg = str(e)

                    # Check if it's a credential error
                    if (
                        "credential" in error_msg.lower()
                        or "expired" in error_msg.lower()
                    ):
                        if retry_count < max_retries:
                            print(
                                "\n⚠️  Credentials expired. Please run 'aws sso login' in another terminal, then press Enter to retry..."
                            )
                            input()
                            print("Retrying with refreshed credentials...")
                            continue
                        else:
                            print(
                                f"\n❌ Credentials still invalid after retries. Batch at index {i} failed."
                            )
                            print(
                                "Items uploaded so far have been saved. Re-run the script after refreshing credentials."
                            )
                            failed += len(batch)
                            pbar.update(len(batch))
                            break
                    else:
                        # Other error
                        if retry_count < max_retries:
                            print(
                                f"\n⚠️  Error uploading batch at index {i} (attempt {retry_count}/{max_retries}): {e}"
                            )
                            print("Retrying in 2 seconds...")
                            import time

                            time.sleep(2)
                        else:
                            print(
                                f"\n❌ Failed to upload batch at index {i} after {max_retries} attempts: {e}"
                            )
                            failed += len(batch)
                            pbar.update(len(batch))
                            break

    return successful, failed, skipped


def main():
    """Main execution function."""
    # Parse command line arguments
    skip_existing = False
    args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]

    # Check for --skip-existing flag
    if "--skip-existing" in sys.argv:
        skip_existing = True
        print("Mode: Skip existing items (resume mode)")
    else:
        print("Mode: Overwrite existing items")
    print()

    if len(args) < 1:
        print(
            "Usage: python load_matches.py <path_to_matches.jsonl> [table_name] [region] [--skip-existing]"
        )
        print("\nExamples:")
        print("  python load_matches.py ../data/matches.jsonl")
        print(
            "  python load_matches.py ../data/matches.jsonl overmatch-matches us-east-1"
        )
        print(
            "  python load_matches.py ../data/matches.jsonl --skip-existing  # Resume mode"
        )
        print("\nFlags:")
        print(
            "  --skip-existing    Skip items already in database (for resuming failed uploads)"
        )
        sys.exit(1)

    jsonl_path = args[0]
    table_name = (
        args[1]
        if len(args) > 1
        else os.getenv("MATCHES_TABLE_NAME", "overmatch-matches")
    )
    region_name = args[2] if len(args) > 2 else os.getenv("AWS_REGION", "us-east-1")

    # Validate file exists
    if not Path(jsonl_path).exists():
        print(f"Error: File not found: {jsonl_path}")
        sys.exit(1)

    print("=" * 60)
    print("Match Data Loader")
    print("=" * 60)
    print(f"Source file: {jsonl_path}")
    print(f"Table name: {table_name}")
    print(f"Region: {region_name}")
    print()

    # Check AWS credentials
    try:
        boto3.client("sts", region_name=region_name).get_caller_identity()
        print("✓ AWS credentials valid")
    except Exception as e:
        print(f"Error: AWS credentials not configured or invalid: {e}")
        print("Run: aws configure")
        sys.exit(1)

    # Check if table exists
    try:
        dynamodb = boto3.resource("dynamodb", region_name=region_name)
        table = dynamodb.Table(table_name)
        table.load()
        print(f"✓ Table '{table_name}' exists")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            print(f"Error: Table '{table_name}' does not exist")
            print("\nCreate it using:")
            print("  aws dynamodb create-table \\")
            print(f"    --table-name {table_name} \\")
            print(
                "    --attribute-definitions AttributeName=element_id,AttributeType=S \\"
            )
            print("    --key-schema AttributeName=element_id,KeyType=HASH \\")
            print("    --billing-mode PAY_PER_REQUEST \\")
            print(f"    --region {region_name}")
            sys.exit(1)
        else:
            print(f"Error checking table: {e}")
            sys.exit(1)

    print()

    # Load JSONL file
    print("Loading matches from JSONL file...")
    matches = load_jsonl(jsonl_path)
    print(f"✓ Loaded {len(matches)} match records")
    print()

    # Group by OSM ID (and convert to Decimal during grouping)
    print("Grouping matches by OSM ID and preparing for DynamoDB...")
    grouped_matches = group_matches_by_osm_id(matches)
    print(f"✓ Grouped into {len(grouped_matches)} unique OSM elements")
    print()

    # Show statistics
    match_counts = [len(m) for m in grouped_matches.values()]
    print("Match statistics:")
    print(f"  Total OSM elements: {len(grouped_matches)}")
    print(f"  Total matches: {sum(match_counts)}")
    print(f"  Average matches per element: {sum(match_counts) / len(match_counts):.2f}")
    print(f"  Min matches: {min(match_counts)}")
    print(f"  Max matches: {max(match_counts)}")
    print()

    # Confirm upload
    response = input("Proceed with upload to DynamoDB? (yes/no): ")
    if response.lower() not in ["yes", "y"]:
        print("Upload cancelled.")
        sys.exit(0)

    print()

    # Upload to DynamoDB
    successful, failed, skipped = upload_to_dynamodb(
        grouped_matches, table_name, region_name, skip_existing=skip_existing
    )

    print()
    print("=" * 60)
    print("Upload Complete")
    print("=" * 60)
    print(f"Successfully uploaded: {successful}")
    print(f"Skipped (already exists): {skipped}")
    print(f"Failed: {failed}")
    print(f"Total processed: {successful + skipped + failed}")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
