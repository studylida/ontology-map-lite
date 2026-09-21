-- Issue #131 one-time demo corpus candidate queries.
-- The first statement records the #112 stratified sample already produced by
-- job_8wgAga2-DTeIDqbll8vhFIDyoYB1. Do not rerun it for #131.
-- Run only the remaining five statements in project ontology-map-gdelt with
-- maximum bytes billed 50 GiB per statement.

WITH candidates AS (
  SELECT
    url,
    MIN(date) AS seen_at,
    COUNTIF(ngram = "SK하이닉스") > 0 AS has_sk,
    COUNTIF(ngram IN ("삼성전자", "삼성 전자", "Samsung Electronics")) > 0 AS has_samsung,
    COUNTIF(ngram IN ("인텔", "Intel", "INTEL")) > 0 AS has_intel,
    COUNTIF(ngram IN ("엔비디아", "NVIDIA", "Nvidia")) > 0 AS has_nvidia
  FROM `gdelt-bq.gdeltv2.webngrams`
  WHERE date >= TIMESTAMP("2026-06-11") AND date < TIMESTAMP("2026-06-26")
    AND lang = "ko"
    AND ngram IN ("SK하이닉스", "삼성전자", "삼성 전자", "Samsung Electronics", "인텔", "Intel", "INTEL", "엔비디아", "NVIDIA", "Nvidia")
  GROUP BY url
), pools AS (
  SELECT
    "base" AS stratum,
    url,
    seen_at,
    ROW_NUMBER() OVER (ORDER BY TO_HEX(SHA256(url))) AS sample_rank,
    COUNT(*) OVER () AS candidate_count
  FROM candidates
  WHERE has_sk
  UNION ALL
  SELECT
    "samsung",
    url,
    seen_at,
    ROW_NUMBER() OVER (ORDER BY TO_HEX(SHA256(url))),
    COUNT(*) OVER ()
  FROM candidates
  WHERE has_sk AND has_samsung
  UNION ALL
  SELECT
    "intel",
    url,
    seen_at,
    ROW_NUMBER() OVER (ORDER BY TO_HEX(SHA256(url))),
    COUNT(*) OVER ()
  FROM candidates
  WHERE has_sk AND has_intel
  UNION ALL
  SELECT
    "nvidia",
    url,
    seen_at,
    ROW_NUMBER() OVER (ORDER BY TO_HEX(SHA256(url))),
    COUNT(*) OVER ()
  FROM candidates
  WHERE has_sk AND has_nvidia
)
SELECT stratum, sample_rank, candidate_count, url, seen_at
FROM pools
WHERE (stratum = "base" AND sample_rank <= 20)
  OR (stratum != "base" AND sample_rank <= 5)
ORDER BY stratum, sample_rank;

WITH candidates AS (
  SELECT
    url,
    FORMAT_TIMESTAMP("%FT%TZ", MIN(date)) AS seen_at,
    COUNTIF(ngram = "SK하이닉스") > 0 AS has_sk,
    COUNTIF(ngram IN ("삼성전자", "삼성 전자", "Samsung Electronics")) > 0 AS has_samsung,
    COUNTIF(ngram IN ("인텔", "Intel", "INTEL")) > 0 AS has_intel,
    COUNTIF(ngram IN ("엔비디아", "NVIDIA", "Nvidia")) > 0 AS has_nvidia
  FROM `gdelt-bq.gdeltv2.webngrams`
  WHERE date >= TIMESTAMP("2026-06-26") AND date < TIMESTAMP("2026-07-11")
    AND lang = "ko"
    AND ngram IN ("SK하이닉스", "삼성전자", "삼성 전자", "Samsung Electronics", "인텔", "Intel", "INTEL", "엔비디아", "NVIDIA", "Nvidia")
  GROUP BY url
)
SELECT "2026-06-26T00:00:00Z" AS query_interval_start, "2026-07-11T00:00:00Z" AS query_interval_end, *
FROM candidates
WHERE has_sk
ORDER BY TO_HEX(SHA256(url));

WITH candidates AS (
  SELECT
    url,
    FORMAT_TIMESTAMP("%FT%TZ", MIN(date)) AS seen_at,
    COUNTIF(ngram = "SK하이닉스") > 0 AS has_sk,
    COUNTIF(ngram IN ("삼성전자", "삼성 전자", "Samsung Electronics")) > 0 AS has_samsung,
    COUNTIF(ngram IN ("인텔", "Intel", "INTEL")) > 0 AS has_intel,
    COUNTIF(ngram IN ("엔비디아", "NVIDIA", "Nvidia")) > 0 AS has_nvidia
  FROM `gdelt-bq.gdeltv2.webngrams`
  WHERE date >= TIMESTAMP("2026-07-11") AND date < TIMESTAMP("2026-07-26")
    AND lang = "ko"
    AND ngram IN ("SK하이닉스", "삼성전자", "삼성 전자", "Samsung Electronics", "인텔", "Intel", "INTEL", "엔비디아", "NVIDIA", "Nvidia")
  GROUP BY url
)
SELECT "2026-07-11T00:00:00Z" AS query_interval_start, "2026-07-26T00:00:00Z" AS query_interval_end, *
FROM candidates
WHERE has_sk
ORDER BY TO_HEX(SHA256(url));

WITH candidates AS (
  SELECT
    url,
    FORMAT_TIMESTAMP("%FT%TZ", MIN(date)) AS seen_at,
    COUNTIF(ngram = "SK하이닉스") > 0 AS has_sk,
    COUNTIF(ngram IN ("삼성전자", "삼성 전자", "Samsung Electronics")) > 0 AS has_samsung,
    COUNTIF(ngram IN ("인텔", "Intel", "INTEL")) > 0 AS has_intel,
    COUNTIF(ngram IN ("엔비디아", "NVIDIA", "Nvidia")) > 0 AS has_nvidia
  FROM `gdelt-bq.gdeltv2.webngrams`
  WHERE date >= TIMESTAMP("2026-07-26") AND date < TIMESTAMP("2026-08-10")
    AND lang = "ko"
    AND ngram IN ("SK하이닉스", "삼성전자", "삼성 전자", "Samsung Electronics", "인텔", "Intel", "INTEL", "엔비디아", "NVIDIA", "Nvidia")
  GROUP BY url
)
SELECT "2026-07-26T00:00:00Z" AS query_interval_start, "2026-08-10T00:00:00Z" AS query_interval_end, *
FROM candidates
WHERE has_sk
ORDER BY TO_HEX(SHA256(url));

WITH candidates AS (
  SELECT
    url,
    FORMAT_TIMESTAMP("%FT%TZ", MIN(date)) AS seen_at,
    COUNTIF(ngram = "SK하이닉스") > 0 AS has_sk,
    COUNTIF(ngram IN ("삼성전자", "삼성 전자", "Samsung Electronics")) > 0 AS has_samsung,
    COUNTIF(ngram IN ("인텔", "Intel", "INTEL")) > 0 AS has_intel,
    COUNTIF(ngram IN ("엔비디아", "NVIDIA", "Nvidia")) > 0 AS has_nvidia
  FROM `gdelt-bq.gdeltv2.webngrams`
  WHERE date >= TIMESTAMP("2026-08-10") AND date < TIMESTAMP("2026-08-25")
    AND lang = "ko"
    AND ngram IN ("SK하이닉스", "삼성전자", "삼성 전자", "Samsung Electronics", "인텔", "Intel", "INTEL", "엔비디아", "NVIDIA", "Nvidia")
  GROUP BY url
)
SELECT "2026-08-10T00:00:00Z" AS query_interval_start, "2026-08-25T00:00:00Z" AS query_interval_end, *
FROM candidates
WHERE has_sk
ORDER BY TO_HEX(SHA256(url));

WITH candidates AS (
  SELECT
    url,
    FORMAT_TIMESTAMP("%FT%TZ", MIN(date)) AS seen_at,
    COUNTIF(ngram = "SK하이닉스") > 0 AS has_sk,
    COUNTIF(ngram IN ("삼성전자", "삼성 전자", "Samsung Electronics")) > 0 AS has_samsung,
    COUNTIF(ngram IN ("인텔", "Intel", "INTEL")) > 0 AS has_intel,
    COUNTIF(ngram IN ("엔비디아", "NVIDIA", "Nvidia")) > 0 AS has_nvidia
  FROM `gdelt-bq.gdeltv2.webngrams`
  WHERE date >= TIMESTAMP("2026-08-25") AND date < TIMESTAMP("2026-09-09")
    AND lang = "ko"
    AND ngram IN ("SK하이닉스", "삼성전자", "삼성 전자", "Samsung Electronics", "인텔", "Intel", "INTEL", "엔비디아", "NVIDIA", "Nvidia")
  GROUP BY url
)
SELECT "2026-08-25T00:00:00Z" AS query_interval_start, "2026-09-09T00:00:00Z" AS query_interval_end, *
FROM candidates
WHERE has_sk
ORDER BY TO_HEX(SHA256(url));
