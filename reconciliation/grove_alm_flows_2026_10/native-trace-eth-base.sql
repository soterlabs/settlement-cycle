SELECT 'ethereum' AS chain, block_time, block_number, CONCAT('0x', TO_HEX(tx_hash)) AS tx_hash,
 CONCAT('0x', TO_HEX("from")) AS sender, CONCAT('0x', TO_HEX("to")) AS receiver,
 CAST(value AS VARCHAR) AS value_wei, trace_address, type, call_type
 FROM ethereum.traces
 WHERE block_time >= TIMESTAMP '2026-10-01' AND block_time < TIMESTAMP '2026-11-01'
 AND block_number >= 26093738 AND block_number < 26316189
 AND ("from" IN (0x491edfb0b8b608044e227225c715981a30f3a44e, 0x0dcd9298e163dfd3c0b5b00f0d9093c36e40a153) OR "to" IN (0x491edfb0b8b608044e227225c715981a30f3a44e, 0x0dcd9298e163dfd3c0b5b00f0d9093c36e40a153)) AND value > 0 AND success AND tx_success
 AND (call_type = 'call' OR type IN ('create', 'suicide', 'reward'))
UNION ALL
SELECT 'base' AS chain, block_time, block_number, CONCAT('0x', TO_HEX(tx_hash)) AS tx_hash,
 CONCAT('0x', TO_HEX("from")) AS sender, CONCAT('0x', TO_HEX("to")) AS receiver,
 CAST(value AS VARCHAR) AS value_wei, trace_address, type, call_type
 FROM base.traces
 WHERE block_time >= TIMESTAMP '2026-10-01' AND block_time < TIMESTAMP '2026-11-01'
 AND block_number >= 52011727 AND block_number < 53350927
 AND ("from" IN (0x9b746dbc5269e1df6e4193bcb441c0fbbf1cecee) OR "to" IN (0x9b746dbc5269e1df6e4193bcb441c0fbbf1cecee)) AND value > 0 AND success AND tx_success
 AND (call_type = 'call' OR type IN ('create', 'suicide', 'reward'))
