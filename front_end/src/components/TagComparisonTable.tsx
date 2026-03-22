import React, { useMemo, useState, useEffect } from "react";
import {
  Table,
  TableHeader,
  TableColumn,
  TableBody,
  TableRow,
  TableCell,
} from "@heroui/table";
import { Button } from "@heroui/button";
import { Chip } from "@heroui/chip";
import { Card } from "@heroui/card";
import { Alert } from "@heroui/alert";
import { Progress } from "@heroui/progress";
import { Checkbox } from "@heroui/checkbox";
import { Link } from "@heroui/link";
import { Tags } from "../objects";
import { MatchInfo } from "../types/matching";
import { Divider } from "@heroui/react";

interface TagComparisonTableProps {
  osmTags: Tags;
  matches: MatchInfo[];
  onApplyTags: (tags: Tags) => void;
  onNoMatch: () => void;
  onSkip: () => void;
}

type TagDiffType = "same" | "different" | "osm-only" | "overture-only";

interface TagComparison {
  key: string;
  osmValue: string | undefined;
  overtureValues: (string | undefined)[];
  diffType: TagDiffType[];
}

// Tags that should be added by default if they don't exist in OSM
const AUTO_ADD_KEYS = ["phone", "website", "cuisine"];
const isAutoAddKey = (key: string): boolean => {
  return AUTO_ADD_KEYS.includes(key) || key.startsWith("addr:");
};

/**
 * Calculate match quality score based on distance and name similarity
 * @param match - Match information containing distance and similarity
 * @returns Quality score from 0-100
 */
const calculateMatchQuality = (match: MatchInfo): number => {
  const normalizedDistance = Math.max(
    0,
    Math.min(1, 1 - match.distance_m / 100),
  );
  const normalizedSimilarity = Math.max(0, (match.similarity - 0.6) / 0.4);
  return (normalizedSimilarity * 0.6 + normalizedDistance * 0.4) * 100;
};

/**
 * Get color for match quality score
 * @param qualityScore - Quality score from 0-100
 * @returns Color string for HeroUI components
 */
const getMatchQualityColor = (
  qualityScore: number,
): "danger" | "warning" | "success" => {
  if (qualityScore < 40) return "danger";
  if (qualityScore < 70) return "warning";
  return "success";
};

const TagComparisonTable: React.FC<TagComparisonTableProps> = ({
  osmTags,
  matches,
  onApplyTags,
  onNoMatch,
  onSkip,
}) => {
  // Track which tags are selected for application
  const [selectedTags, setSelectedTags] = useState<Set<string>>(new Set());
  // Track whether match details are expanded
  const [detailsExpanded, setDetailsExpanded] = useState(false);

  // Merge all matches with priority to the closest one (by distance)
  const mergedOvertureTags = useMemo(() => {
    // Sort matches by distance (closest first)
    const sortedMatches = [...matches].sort(
      (a, b) => a.distance_m - b.distance_m,
    );

    const merged: Tags = {};
    // Process matches in reverse order so closest match has priority
    for (let i = sortedMatches.length - 1; i >= 0; i--) {
      Object.entries(sortedMatches[i].overture_tags).forEach(([key, value]) => {
        if (value !== undefined && value !== null) {
          merged[key] = String(value);
        }
      });
    }
    return merged;
  }, [matches]);

  // Build tag comparison data
  const tagComparisons = useMemo(() => {
    const allKeys = new Set<string>();

    // Collect all unique keys from OSM and merged Overture tags
    Object.keys(osmTags).forEach((key) => allKeys.add(key));
    Object.keys(mergedOvertureTags).forEach((key) => allKeys.add(key));

    const comparisons: TagComparison[] = [];

    // Handle smart mapping: if OSM has contact:phone and we have phone,
    // compare them together instead of treating as separate tags
    const hasContactPhone = osmTags["contact:phone"] !== undefined;
    const hasPhone = mergedOvertureTags["phone"] !== undefined;

    if (hasContactPhone && hasPhone) {
      // Map phone to contact:phone for comparison
      const osmContactPhoneValue = osmTags["contact:phone"];
      const overturePhoneValue = mergedOvertureTags["phone"];

      let diffType: TagDiffType;
      if (osmContactPhoneValue === overturePhoneValue) {
        diffType = "same";
      } else {
        diffType = "different";
      }

      comparisons.push({
        key: "contact:phone",
        osmValue: osmContactPhoneValue,
        overtureValues: [overturePhoneValue],
        diffType: [diffType],
      });

      // Remove these keys from the set so they're not processed again
      allKeys.delete("contact:phone");
      allKeys.delete("phone");
    }

    allKeys.forEach((key) => {
      const osmValue = osmTags[key];
      const overtureValue = mergedOvertureTags[key];

      let diffType: TagDiffType;
      if (osmValue === undefined && overtureValue === undefined) {
        diffType = "same";
      } else if (osmValue === undefined) {
        diffType = "overture-only";
      } else if (overtureValue === undefined) {
        diffType = "osm-only";
      } else {
        diffType = osmValue === overtureValue ? "same" : "different";
      }

      // Only show if there's a difference or if it exists in either
      const hasInterest = diffType !== "same" || osmValue !== undefined;

      if (hasInterest) {
        comparisons.push({
          key,
          osmValue,
          overtureValues: [overtureValue],
          diffType: [diffType],
        });
      }
    });

    // Sort: differences first, then overture-only, then by key name
    comparisons.sort((a, b) => {
      const aDiff = a.diffType[0] === "different";
      const bDiff = b.diffType[0] === "different";
      if (aDiff && !bDiff) return -1;
      if (!aDiff && bDiff) return 1;

      const aOverture = a.diffType[0] === "overture-only";
      const bOverture = b.diffType[0] === "overture-only";
      if (aOverture && !bOverture) return -1;
      if (!aOverture && bOverture) return 1;

      return a.key.localeCompare(b.key);
    });

    return comparisons;
  }, [osmTags, mergedOvertureTags]);

  // Initialize selected tags based on smart defaults
  useEffect(() => {
    const defaultSelected = new Set<string>();

    tagComparisons.forEach((comparison) => {
      const overtureValue = comparison.overtureValues[0];
      const osmValue = comparison.osmValue;

      // Only consider tags that exist in Overture
      if (overtureValue !== undefined) {
        // If OSM doesn't have this tag and it's an auto-add key, select it
        if (osmValue === undefined && isAutoAddKey(comparison.key)) {
          defaultSelected.add(comparison.key);
        }
        // If OSM has the tag, don't select it (keep OSM value by default)
      }
    });

    setSelectedTags(defaultSelected);
  }, [tagComparisons]);

  const handleTagToggle = (key: string) => {
    setSelectedTags((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  const getDiffColor = (
    diffType: TagDiffType,
  ): "default" | "primary" | "secondary" | "success" | "warning" | "danger" => {
    switch (diffType) {
      case "same":
        return "success";
      case "different":
        return "warning";
      case "osm-only":
        return "primary";
      case "overture-only":
        return "secondary";
      default:
        return "default";
    }
  };

  const linkButton = (href?: string) => (
    <div className="flex flex-row items-center gap-2">
      {href}
      <Button
        as={Link}
        href={href}
        target="_blank"
        color="primary"
        size="sm"
        radius="full"
        isIconOnly
        showAnchorIcon
      />
    </div>
  );

  const handleApplySelected = () => {
    const newTags: Tags = { ...osmTags };

    // Apply only selected Overture tags from merged tags
    selectedTags.forEach((key) => {
      // Special case: contact:phone maps to our phone value
      if (key === "contact:phone") {
        const phoneValue = mergedOvertureTags["phone"];
        if (phoneValue !== undefined && phoneValue !== null) {
          newTags["contact:phone"] = String(phoneValue);
        }
      } else {
        const value = mergedOvertureTags[key];
        if (value !== undefined && value !== null) {
          newTags[key] = String(value);
        }
      }
    });

    onApplyTags(newTags);
  };

  // Safety check for empty matches
  if (!matches || matches.length === 0) {
    return (
      <Card className="p-4">
        <p className="text-sm text-gray-600">
          No matches available to compare.
        </p>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {matches.length > 1 && (
        <Alert
          title="Multiple Overture matches"
          description="Tags have been merged with priority to the closest match."
          color="warning"
        />
      )}

      <>
        <Table aria-label="Tag comparison table" className="mb-4" isCompact>
          <TableHeader>
            <TableColumn>APPLY</TableColumn>
            <TableColumn>KEY</TableColumn>
            <TableColumn>OSM VALUE</TableColumn>
            <TableColumn>
              OVERTURE VALUE{matches.length > 1 ? " (MERGED)" : ""}
            </TableColumn>
            <TableColumn>STATUS</TableColumn>
          </TableHeader>
          <TableBody>
            {tagComparisons.length === 0 ? (
              <TableRow>
                <TableCell>-</TableCell>
                <TableCell>No differences found</TableCell>
                <TableCell>-</TableCell>
                <TableCell>-</TableCell>
                <TableCell>-</TableCell>
              </TableRow>
            ) : (
              tagComparisons.map((comparison) => {
                const overtureValue = comparison.overtureValues[0];
                const canApply = overtureValue !== undefined;

                return (
                  <TableRow key={comparison.key}>
                    <TableCell>
                      {canApply ? (
                        <Checkbox
                          isSelected={selectedTags.has(comparison.key)}
                          onValueChange={() => handleTagToggle(comparison.key)}
                          size="sm"
                        />
                      ) : (
                        <span className="text-gray-400">-</span>
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-sm">
                      {comparison.key}
                    </TableCell>
                    <TableCell className="font-mono text-sm">
                      {comparison.key === "website" && comparison.osmValue
                        ? linkButton(comparison.osmValue)
                        : comparison.osmValue || (
                            <span className="text-gray-400">-</span>
                          )}
                    </TableCell>
                    <TableCell className="font-mono text-sm">
                      {comparison.key === "website" && overtureValue
                        ? linkButton(overtureValue)
                        : overtureValue || (
                            <span className="text-gray-400">-</span>
                          )}
                    </TableCell>
                    <TableCell>
                      <Chip
                        size="sm"
                        color={getDiffColor(comparison.diffType[0])}
                        variant="flat"
                      >
                        {comparison.diffType[0]}
                      </Chip>
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
        <div className="flex flex-col gap-3">
          {/* Primary workflow - prominent and grouped */}
          <div className="flex flex-wrap gap-2 justify-end">
            <Button
              color="secondary"
              size="lg"
              onPress={onNoMatch}
              className="flex-1 sm:flex-none min-w-40"
            >
              Nothing to add
            </Button>
            <Button
              color="primary"
              size="lg"
              onPress={handleApplySelected}
              isDisabled={selectedTags.size === 0}
              className="flex-1 sm:flex-none min-w-40"
            >
              Apply tags
              <Chip size="sm">
                <span className="font-mono">{selectedTags.size}</span>
              </Chip>
            </Button>
          </div>

          {/* Secondary actions - smaller, less prominent */}
          <div className="flex flex-wrap gap-2 justify-end text-sm">
            <Button
              color="danger"
              variant="light"
              size="sm"
              onPress={onNoMatch}
              className="flex-1 sm:flex-none"
            >
              Not a match
            </Button>
            <Button
              color="default"
              variant="flat"
              size="sm"
              onPress={onSkip}
              className="flex-1 sm:flex-none"
            >
              Skip
            </Button>
          </div>
        </div>
      </>

      {matches[0] && (
        <Card className="p-4">
          {(() => {
            const closestMatch = [...matches].sort(
              (a, b) => a.distance_m - b.distance_m,
            )[0];

            return (
              <>
                <div className="flex flex-col sm:flex-row sm:justify-between sm:items-start gap-2 mb-2">
                  <h4 className="text-sm font-semibold">
                    {matches.length > 1
                      ? "Closest Match Details (Priority)"
                      : "Match Details"}
                  </h4>
                  <div className="flex flex-col items-start sm:items-end gap-1">
                    <span className="text-xs text-gray-600">Overture ID:</span>
                    <span className="font-mono text-xs break-all">
                      {closestMatch.overture_id}
                    </span>
                  </div>
                </div>

                <div className="mb-3">
                  <div className="flex justify-between items-center mb-1">
                    <span className="text-xs font-medium">Match Quality</span>
                    <span className="text-xs font-medium">
                      {calculateMatchQuality(closestMatch).toFixed(0)}%
                    </span>
                  </div>
                  <Progress
                    value={calculateMatchQuality(closestMatch)}
                    color={getMatchQualityColor(
                      calculateMatchQuality(closestMatch),
                    )}
                    size="sm"
                    aria-label="Match quality score"
                  />
                </div>

                <Button
                  size="sm"
                  variant="light"
                  onPress={() => setDetailsExpanded(!detailsExpanded)}
                  className="w-full mb-2"
                >
                  {detailsExpanded ? "Hide Details" : "Show Details"}
                </Button>

                {detailsExpanded && (
                  <>
                    <Divider className="my-2" />
                    <div className="grid grid-cols-2 gap-2 text-sm">
                      <div>
                        <span className="font-medium">Distance:</span>{" "}
                        {closestMatch.distance_m.toFixed(1)}m
                      </div>
                      <div>
                        <span className="font-medium">Name similarity:</span>{" "}
                        {(closestMatch.similarity * 100).toFixed(1)}%
                      </div>
                      <div className="col-span-2">
                        <span className="font-medium">Location:</span>{" "}
                        {closestMatch.lat.toFixed(5)},{" "}
                        {closestMatch.lon.toFixed(5)}
                      </div>
                    </div>
                  </>
                )}
              </>
            );
          })()}
        </Card>
      )}
    </div>
  );
};

export default TagComparisonTable;
