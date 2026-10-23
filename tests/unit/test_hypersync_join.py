from types import SimpleNamespace

from settle.extract import hypersync_store, postgres_store


def test_joined_log_projection_is_forwarded_and_only_retains_requested_events(monkeypatch):
    monkeypatch.setenv('ENVIO_API_TOKEN', 'test')
    monkeypatch.setattr(postgres_store, '_get_conn', lambda: None)
    selected = [{'address': ['0xabc'], 'topics': [['0xmint'], ['0xholder']]}]
    calls = []

    def post(url, *, json, **kwargs):
        calls.append(json)
        assert json['join_mode'] == 'JoinAll' and json['logs'] == selected
        assert json['max_num_logs'] == json['max_num_blocks'] == json['max_num_transactions'] == 100_000
        return SimpleNamespace(status_code=200, ok=True, json=lambda: {
            'archive_height': 1000, 'next_block': 11,
            'data': [{'blocks': [{'number': 10, 'timestamp': 123}], 'logs': [
                {'block_number': 10, 'log_index': 1, 'address': '0xabc', 'topic0': '0xmint'},
                {'block_number': 10, 'log_index': 2, 'address': '0xdef', 'topic0': '0xmessage'},
            ]}],
        })

    rows = hypersync_store.fetch_logs('base', selected, 10, 10, join_mode='JoinAll',
                                     result_topic0='0xmessage', post=post)
    assert len(calls) == len(rows) == 1
    assert rows[0].address == '0xdef' and rows[0].block_time == 123


def test_join_and_projection_cannot_reuse_normal_log_cache():
    args = ('base', [{'address': ['0xabc']}])
    normal = hypersync_store._stream_key(*args)
    joined = hypersync_store._stream_key(*args, join_mode='JoinAll')
    projected = hypersync_store._stream_key(*args, join_mode='JoinAll', result_topic0='0xaaa')
    other = hypersync_store._stream_key(*args, join_mode='JoinAll', result_topic0='0xbbb')
    assert len({normal, joined, projected, other}) == 4
