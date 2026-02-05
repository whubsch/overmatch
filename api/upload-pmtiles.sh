#!/usr/bin/env bash
#
# Upload PMTiles file to S3 with proper AWS credential handling
#
# This script wraps the Python upload script and handles AWS credential export
# needed for boto3 to access S3 when credentials are stored in a credential helper.
#
# Usage:
#   ./upload-pmtiles.sh [OPTIONS]
#
# Options:
#   -b, --bucket NAME    S3 bucket name (required if BUCKET_NAME not set)
#   -f, --file PATH      Path to PMTiles file (default: ../data/matches_enriched.pmtiles)
#   -k, --key KEY        S3 object key (default: matches_enriched.pmtiles)
#   -r, --region REGION  AWS region (default: us-east-1)
#   -h, --help           Show this help message
#
# Environment Variables:
#   BUCKET_NAME          S3 bucket name
#   AWS_REGION           AWS region
#
# Examples:
#   ./upload-pmtiles.sh --bucket my-pmtiles-bucket
#   BUCKET_NAME=my-bucket ./upload-pmtiles.sh
#   ./upload-pmtiles.sh -b my-bucket -k tiles/matches.pmtiles
#

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Default values
PMTILES_FILE="${PROJECT_ROOT}/data/matches_enriched.pmtiles"
BUCKET_NAME="${BUCKET_NAME:-overmatch-pmtiles}"
OBJECT_KEY="matches_enriched.pmtiles"
AWS_REGION="${AWS_REGION:-us-east-1}"

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -b|--bucket)
            BUCKET_NAME="$2"
            shift 2
            ;;
        -f|--file)
            PMTILES_FILE="$2"
            shift 2
            ;;
        -k|--key)
            OBJECT_KEY="$2"
            shift 2
            ;;
        -r|--region)
            AWS_REGION="$2"
            shift 2
            ;;
        -h|--help)
            echo "Upload PMTiles file to S3"
            echo ""
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  -b, --bucket NAME    S3 bucket name (required if BUCKET_NAME not set)"
            echo "  -f, --file PATH      Path to PMTiles file (default: ../data/matches_enriched.pmtiles)"
            echo "  -k, --key KEY        S3 object key (default: matches_enriched.pmtiles)"
            echo "  -r, --region REGION  AWS region (default: us-east-1)"
            echo "  -h, --help           Show this help message"
            echo ""
            echo "Environment Variables:"
            echo "  BUCKET_NAME          S3 bucket name"
            echo "  AWS_REGION           AWS region"
            echo ""
            echo "Examples:"
            echo "  $0 --bucket my-pmtiles-bucket"
            echo "  BUCKET_NAME=my-bucket $0"
            echo "  $0 -b my-bucket -k tiles/matches.pmtiles"
            exit 0
            ;;
        *)
            echo -e "${RED}Error: Unknown option: $1${NC}" >&2
            echo "Use --help for usage information" >&2
            exit 1
            ;;
    esac
done

echo "==========================================================="
echo "PMTiles Upload with AWS Credential Management"
echo "==========================================================="
echo ""

# Bucket name should now have a default, but keep validation just in case
if [ -z "$BUCKET_NAME" ]; then
    echo -e "${RED}Error: Bucket name required${NC}" >&2
    echo "  Provide with --bucket or set BUCKET_NAME environment variable" >&2
    echo "  Use --help for more information" >&2
    exit 1
fi

# Check if PMTiles file exists
if [ ! -f "$PMTILES_FILE" ]; then
    echo -e "${RED}Error: PMTiles file not found: $PMTILES_FILE${NC}" >&2
    echo "  Run './scripts/build_pmtiles_with_credentials.sh production' first" >&2
    exit 1
fi

# Get file size
FILE_SIZE=$(du -h "$PMTILES_FILE" | cut -f1)
echo -e "${BLUE}File:${NC}   $PMTILES_FILE ($FILE_SIZE)"
echo -e "${BLUE}Bucket:${NC} $BUCKET_NAME"
echo -e "${BLUE}Key:${NC}    $OBJECT_KEY"
echo -e "${BLUE}Region:${NC} $AWS_REGION"
echo ""

# Check if AWS CLI is available
if ! command -v aws &> /dev/null; then
    echo -e "${RED}Error: AWS CLI not found. Please install it first.${NC}" >&2
    echo "Visit: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html" >&2
    exit 1
fi

# Check if Python 3.12 is available
if ! command -v uv run &> /dev/null; then
    echo -e "${RED}Error: Python 3.12 not found. Please install it first.${NC}" >&2
    exit 1
fi

# Check if Python script exists
PYTHON_SCRIPT="${SCRIPT_DIR}/upload_pmtiles.py"
if [ ! -f "$PYTHON_SCRIPT" ]; then
    echo -e "${RED}Error: Upload script not found: $PYTHON_SCRIPT${NC}" >&2
    exit 1
fi

# Check AWS credentials
echo "Checking AWS credentials..."
if ! aws sts get-caller-identity &> /dev/null; then
    echo -e "${RED}Error: AWS credentials not configured or expired.${NC}" >&2
    echo "Please configure AWS credentials using 'aws configure' or log in via AWS SSO." >&2
    exit 1
fi

# Get caller identity for verification
CALLER_IDENTITY=$(aws sts get-caller-identity --output json 2>/dev/null)
ACCOUNT=$(echo "$CALLER_IDENTITY" | grep -o '"Account": "[^"]*"' | cut -d'"' -f4)
ARN=$(echo "$CALLER_IDENTITY" | grep -o '"Arn": "[^"]*"' | cut -d'"' -f4)

echo -e "${GREEN}✓ AWS credentials found${NC}"
echo "  Account: $ACCOUNT"
echo "  ARN: $ARN"
echo ""

# Export credentials to environment variables for boto3
echo "Exporting credentials for boto3..."
if aws configure export-credentials --format env &> /dev/null; then
    # Export credentials to current environment
    eval "$(aws configure export-credentials --format env)"
    echo -e "${GREEN}✓ Credentials exported successfully${NC}"

    # Check if credentials have expiration
    if [ -n "$AWS_CREDENTIAL_EXPIRATION" ]; then
        echo "  Note: Credentials expire at $AWS_CREDENTIAL_EXPIRATION"
    fi
else
    echo -e "${YELLOW}Warning: Could not export credentials using 'aws configure export-credentials'${NC}" >&2
    echo "Attempting to continue anyway (credentials may already be in environment)..." >&2
fi

echo ""
echo "==========================================================="
echo "Running upload_pmtiles.py"
echo "==========================================================="
echo ""

# Run the Python upload script with proper arguments
uv run "$PYTHON_SCRIPT" \
    "$PMTILES_FILE" \
    --bucket "$BUCKET_NAME" \
    --key "$OBJECT_KEY" \
    --region "$AWS_REGION"

# Capture exit code
EXIT_CODE=$?

echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo "==========================================================="
    echo -e "${GREEN}✓ Upload completed successfully!${NC}"
    echo "==========================================================="
else
    echo "==========================================================="
    echo -e "${RED}✗ Upload failed with exit code $EXIT_CODE${NC}" >&2
    echo "==========================================================="
fi

exit $EXIT_CODE
