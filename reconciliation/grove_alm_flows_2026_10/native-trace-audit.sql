SELECT 'avalanche_c' AS chain, block_time, block_number, CONCAT('0x', TO_HEX(tx_hash)) AS tx_hash,
 CONCAT('0x', TO_HEX("from")) AS sender, CONCAT('0x', TO_HEX("to")) AS receiver,
 CAST(value AS VARCHAR) AS value_wei, trace_address, type, call_type
 FROM avalanche_c.traces
 WHERE block_time >= TIMESTAMP '2026-10-01' AND block_time < TIMESTAMP '2026-11-01'
 AND block_number >= 96488900 AND block_number < 99082939
 AND ("from" = 0x7107dd8f56642327945294a18a4280c78e153644 OR "to" = 0x7107dd8f56642327945294a18a4280c78e153644) AND value > 0 AND success AND tx_success
 AND (call_type = 'call' OR type IN ('create', 'suicide', 'reward'))
UNION ALL
SELECT 'monad' AS chain, block_time, block_number, CONCAT('0x', TO_HEX(tx_hash)) AS tx_hash,
 CONCAT('0x', TO_HEX("from")) AS sender, CONCAT('0x', TO_HEX("to")) AS receiver,
 CAST(value AS VARCHAR) AS value_wei, trace_address, type, call_type
 FROM monad.traces
 WHERE block_time >= TIMESTAMP '2026-10-01' AND block_time < TIMESTAMP '2026-11-01'
 AND block_number >= 109459391 AND block_number < 118321697
 AND ("from" = 0x94b398acb2fce988871218221ea6a4a2b26cccbc OR "to" = 0x94b398acb2fce988871218221ea6a4a2b26cccbc) AND value > 0 AND success AND tx_success
 AND (call_type = 'call' OR type IN ('create', 'suicide', 'reward'))
UNION ALL
SELECT 'plume' AS chain, block_time, block_number, CONCAT('0x', TO_HEX(tx_hash)) AS tx_hash,
 CONCAT('0x', TO_HEX("from")) AS sender, CONCAT('0x', TO_HEX("to")) AS receiver,
 CAST(value AS VARCHAR) AS value_wei, trace_address, type, call_type
 FROM plume.traces
 WHERE block_time >= TIMESTAMP '2026-10-01' AND block_time < TIMESTAMP '2026-11-01'
 AND block_number >= 96561571 AND block_number < 102924354
 AND ("from" = 0x1db91ad50446a671e2231f77e00948e68876f812 OR "to" = 0x1db91ad50446a671e2231f77e00948e68876f812) AND value > 0 AND success AND tx_success
 AND (call_type = 'call' OR type IN ('create', 'suicide', 'reward'))
