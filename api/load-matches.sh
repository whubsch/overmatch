#!/bin/bash

# Convenience script for loading match data into DynamoDB
# This script wraps the Python load_matches.py script with better defaults

set -e  # Exit on error

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEFAULT_MATCHES_FILE="$PROJECT_ROOT/data/matches.jsonl"

# AWS Configuration (can be overridden by environment variables)
ENVIRONMENT="${ENVIRONMENT:-production}"
AWS_REGION="${AWS_REGION:-us-east-1}"
TABLE_NAME="${MATCHES_TABLE_NAME:-overmatch-matches-$ENVIRONMENT}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Overmatch Matches Data Loader${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: Python 3 is not installed${NC}"
    exit 1
fi

# Check if AWS CLI is installed
if ! command -v aws &> /dev/null; then
    echo -e "${RED}Error: AWS CLI is not installed${NC}"
    echo "Install it from: https://aws.amazon.com/cli/"
    exit 1
fi

# Parse command line arguments
SKIP_EXISTING_FLAG=""
if [[ "$*" == *"--skip-existing"* ]]; then
    SKIP_EXISTING_FLAG="--skip-existing"
    echo -e "${YELLOW}Mode: Resume mode (skip existing items)${NC}"
else
    echo -e "${YELLOW}Mode: Overwrite mode (update all items)${NC}"
fi
echo ""

# Get the matches file (first non-flag argument)
MATCHES_FILE=""
for arg in "$@"; do
    if [[ "$arg" != --* ]]; then
        MATCHES_FILE="$arg"
        break
    fi
done
MATCHES_FILE="${MATCHES_FILE:-$DEFAULT_MATCHES_FILE}"

if [ ! -f "$MATCHES_FILE" ]; then
    echo -e "${RED}Error: Matches file not found: $MATCHES_FILE${NC}"
    echo ""
    echo "Usage: $0 [path-to-matches.jsonl] [--skip-existing]"
    echo ""
    echo "Examples:"
    echo "  $0                                    # Uses default, overwrites existing"
    echo "  $0 /path/to/custom/matches.jsonl     # Uses custom file"
    echo "  $0 --skip-existing                    # Resume mode, skip existing items"
    echo "  $0 /path/to/custom.jsonl --skip-existing"
    echo ""
    echo "Flags:"
    echo "  --skip-existing      Skip items already in database (for resuming)"
    echo ""
    echo "Environment variables:"
    echo "  ENVIRONMENT          # Environment name (default: production)"
    echo "  AWS_REGION           # AWS region (default: us-east-1)"
    echo "  MATCHES_TABLE_NAME   # Full table name (overrides environment-based naming)"
    exit 1
fi

echo "Configuration:"
echo "  Matches file: $MATCHES_FILE"
echo "  Table name: $TABLE_NAME"
echo "  Region: $AWS_REGION"
echo "  Environment: $ENVIRONMENT"
echo ""

# Check AWS credentials
echo -e "${YELLOW}Checking AWS credentials...${NC}"
if ! aws sts get-caller-identity &> /dev/null; then
    echo -e "${RED}Error: AWS credentials not configured${NC}"
    echo "Run: aws configure"
    exit 1
fi
echo -e "${GREEN}✓ AWS credentials valid${NC}"
echo ""

# Check if table exists
echo -e "${YELLOW}Checking if table exists...${NC}"
if aws dynamodb describe-table --table-name "$TABLE_NAME" --region "$AWS_REGION" &> /dev/null; then
    echo -e "${GREEN}✓ Table '$TABLE_NAME' exists${NC}"
else
    echo -e "${RED}Error: Table '$TABLE_NAME' does not exist${NC}"
    echo ""
    echo "Please deploy the CloudFormation stack first:"
    echo "  cd $SCRIPT_DIR"
    echo "  ./deploy.sh"
    echo ""
    echo "Or create the table manually:"
    echo "  aws dynamodb create-table \\"
    echo "    --table-name $TABLE_NAME \\"
    echo "    --attribute-definitions AttributeName=element_id,AttributeType=S \\"
    echo "    --key-schema AttributeName=element_id,KeyType=HASH \\"
    echo "    --billing-mode PAY_PER_REQUEST \\"
    echo "    --region $AWS_REGION"
    exit 1
fi
echo ""

# Export AWS credentials for boto3
# The AWS CLI can access SSO credentials, but boto3 needs them explicitly exported
echo -e "${YELLOW}Exporting AWS credentials for boto3...${NC}"
eval $(aws configure export-credentials --format env 2>/dev/null || true)

# Run the Python loader script
echo -e "${YELLOW}Running data loader...${NC}"
echo ""

uv run "$SCRIPT_DIR/load_matches.py" "$MATCHES_FILE" "$TABLE_NAME" "$AWS_REGION" $SKIP_EXISTING_FLAG

EXIT_CODE=$?

echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}Data loading complete!${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo "You can now query matches via the API:"
    echo "  GET /matches?osm_ids=way/123,node/456"
else
    echo -e "${RED}========================================${NC}"
    echo -e "${RED}Data loading failed!${NC}"
    echo -e "${RED}========================================${NC}"
    exit $EXIT_CODE
fi
