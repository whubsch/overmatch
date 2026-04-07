import { OsmElement, OsmNode, OsmWay, OsmRelation } from "../objects";

interface WikidataResult {
  area: string;
  label: string;
  osm_id: string;
}

interface WikidataResponse {
  head: {
    vars: string[];
  };
  results: {
    bindings: Array<{
      area: { type: string; value: string };
      label: { type: string; value: string };
      osm_id: { type: string; value: string };
    }>;
  };
}

interface OsmAmenityBinding {
  id: { type: string; value: string };
  name: { type: string; value: string };
  centroid: { type: string; value: string };
}

interface OsmAmenityResponse {
  head: {
    vars: string[];
  };
  results: {
    bindings: OsmAmenityBinding[];
  };
}

async function fetchWikidataAdministrativeAreas(
  count: number = 3,
): Promise<WikidataResult[]> {
  try {
    const sparqlQuery = `
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
SELECT DISTINCT ?area ?label ?osm_id WHERE {
  ?area wdt:P31/wdt:P279* ?type;      # instance/subclass of
        wdt:P17 wd:Q30;               # country: United States
        wdt:P402 ?osm_id.             # has OSM relation ID
  ?area rdfs:label ?label

  VALUES ?type { wd:Q47168 wd:Q17343829 }  # US administrative entity types

  FILTER (LANG(?label) = "en") .
  }
ORDER BY RAND()
LIMIT ${count}
`;

    const endpoint = "https://qlever.dev/api/wikidata/";
    const params = new URLSearchParams({
      query: sparqlQuery,
      format: "json",
    });

    const url = `${endpoint}?${params.toString()}`;

    const response = await fetch(url);

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const data: WikidataResponse = await response.json();

    // Transform the response to a more usable format
    const transformedResults: WikidataResult[] = data.results.bindings.map(
      (binding) => ({
        area: binding.area.value,
        label: binding.label.value,
        osm_id: binding.osm_id.value,
      }),
    );

    return transformedResults;
  } catch (error) {
    console.error("Error fetching Wikidata administrative areas:", error);
    throw error;
  }
}

/**
 * Parse WKT POINT string to coordinates
 * Example: "POINT(12.345 67.890)" -> { lon: 12.345, lat: 67.890 }
 */
function parseWktPoint(wkt: string): { lon: number; lat: number } | null {
  const match = wkt.match(/POINT\(([-\d.]+)\s+([-\d.]+)\)/);
  if (!match) return null;
  return {
    lon: parseFloat(match[1]),
    lat: parseFloat(match[2]),
  };
}

/**
 * Extract OSM type and ID from QLever URI
 * Example: "https://www.openstreetmap.org/node/123456" -> { type: "node", id: 123456 }
 */
function parseOsmUri(
  uri: string,
): { type: "node" | "way" | "relation"; id: number } | null {
  const match = uri.match(/openstreetmap\.org\/(node|way|relation)\/(\d+)/);
  if (!match) return null;
  const type = match[1] as "node" | "way" | "relation";
  const id = parseInt(match[2], 10);
  return { type, id };
}

/**
 * Fetches amenities within an OSM relation using QLever
 * @param relationId - OSM relation ID
 * @returns Promise<OsmElement[]> - Array of OSM elements
 */
export async function fetchAmenitiesInRelation(
  relationId: string,
): Promise<OsmElement[]> {
  try {
    const sparqlQuery = `
PREFIX osmkey: <https://www.openstreetmap.org/wiki/Key:>
PREFIX osmrel: <https://www.openstreetmap.org/relation/>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX osm: <https://www.openstreetmap.org/>
PREFIX ogc: <http://www.opengis.net/rdf#>
PREFIX geo: <http://www.opengis.net/ont/geosparql#>
PREFIX geof: <http://www.opengis.net/def/function/geosparql/>
SELECT ?id ?name ?centroid WHERE {
  osmrel:${relationId} ogc:sfIntersects ?id .
  ?id osmkey:name ?name .
  ?id geo:hasGeometry/geo:asWKT ?geometry .
  BIND(geof:centroid(?geometry) AS ?centroid)
  FILTER NOT EXISTS { ?id osmkey:brand:wikidata ?wikidata . }
  {
    VALUES ?amenity_types {
      "restaurant" "bar" "pub" "fast_food" "cafe"
    }
    ?id osmkey:amenity ?amenity_types .
  }
  UNION
  {
    ?id osmkey:shop "beauty" .
    ?id osmkey:beauty "nails" .
  }
  UNION
  {
    VALUES ?tourism_types {
      "hotel" "motel" "hostel" "resort"
    }
    ?id osmkey:tourism ?tourism_types .
  }
}
`;

    const endpoint = "https://qlever.dev/api/osm-planet";
    const params = new URLSearchParams({
      query: sparqlQuery,
      format: "json",
    });

    const url = `${endpoint}?${params.toString()}`;

    const response = await fetch(url);

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const data: OsmAmenityResponse = await response.json();

    // Transform QLever results to OsmElement format
    const osmElements: OsmElement[] = data.results.bindings
      .map((binding): OsmElement | null => {
        const osmInfo = parseOsmUri(binding.id.value);
        const coords = parseWktPoint(binding.centroid.value);

        if (!osmInfo || !coords) {
          console.warn("Failed to parse OSM element:", binding.id.value);
          return null;
        }

        const baseElement = {
          id: osmInfo.id,
          tags: {
            name: binding.name.value,
          },
          version: 1, // QLever doesn't provide version info
          user: "", // QLever doesn't provide user info
        };

        // Create appropriate element type based on OSM type
        if (osmInfo.type === "node") {
          const nodeElement: OsmNode = {
            ...baseElement,
            type: "node",
            lat: coords.lat,
            lon: coords.lon,
          };
          return nodeElement;
        } else if (osmInfo.type === "way") {
          const wayElement: OsmWay = {
            ...baseElement,
            type: "way",
            bounds: {
              minlat: coords.lat,
              minlon: coords.lon,
              maxlat: coords.lat,
              maxlon: coords.lon,
            },
            nodes: [],
            geometry: [],
            center: coords,
          };
          return wayElement;
        } else if (osmInfo.type === "relation") {
          const relationElement: OsmRelation = {
            ...baseElement,
            type: "relation",
            bounds: {
              minlat: coords.lat,
              minlon: coords.lon,
              maxlat: coords.lat,
              maxlon: coords.lon,
            },
            members: [],
            center: coords,
          };
          return relationElement;
        }

        return null;
      })
      .filter((element): element is OsmElement => element !== null);

    return osmElements;
  } catch (error) {
    console.error("Error fetching amenities from QLever:", error);
    throw error;
  }
}

export default fetchWikidataAdministrativeAreas;
