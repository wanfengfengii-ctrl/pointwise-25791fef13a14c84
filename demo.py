import json
from pathlib import Path
from tempfile import TemporaryDirectory

from objects.delete_queue import query_delete
from objects.execution import execute_delete
from objects.store import initialize_object_store
from publish.catalog import initialize_catalog
from publish.submissions import submit
from retire.dispatch import collect
from retire.volumes import retire_volume
from review.result import read
from review.selection import RetiredVolumeError


def pickup_fields(result):
    return {'records': list(result.records), 'count': result.record_count, 'peak': result.peak}


def emit(case_id, records, expected, actual, expected_effect, actual_effect, paths, oracle):
    if actual != expected or actual_effect != expected_effect:
        raise AssertionError(f'{case_id} expected {expected!r} and {expected_effect!r}, '
                             f'got {actual!r} and {actual_effect!r}')
    evidence = {
        'case_id': case_id,
        'input': records,
        'expected': expected,
        'actual': actual,
        'downstream_expected': expected_effect,
        'downstream_actual': actual_effect,
        'code_paths': paths,
        'oracle': oracle,
    }
    print('POINTWISE_SCENARIO ' + json.dumps(evidence, ensure_ascii=False, sort_keys=True))


def require_receipt(result):
    if result.receipt is None:
        raise AssertionError(f'{result.request_id} did not sign')
    return result.receipt


def main():
    samples = json.loads(Path(__file__).with_name('samples.json').read_text(encoding='utf-8'))
    with TemporaryDirectory() as directory:
        catalog_path = Path(directory) / 'catalog.sqlite3'
        object_path = Path(directory) / 'objects.sqlite3'
        catalog = initialize_catalog(catalog_path)
        objects = initialize_object_store(object_path)
        shift = require_receipt(submit(catalog, objects, 'C', samples['shift']['records']))
        empty = require_receipt(submit(catalog, objects, 'E', samples['empty']['records']))
        catalog = initialize_catalog(catalog_path)
        objects = initialize_object_store(object_path)

        for case, key, receipt, oracle in (
            ('v1', 'shift', shift, 'Count three positions including the repeated 3, then compare 3, 8 and 3.'),
            ('v2', 'empty', empty, 'An empty list has zero positions and no peak.'),
        ):
            expected = samples[key]
            actual = read(catalog, objects, receipt.volume_id)
            emit(case, expected['records'], expected, pickup_fields(actual), receipt.volume_id,
                 actual.volume_id, ['publish/submissions.py', 'review/records.py', 'review/statistics.py'],
                 oracle)

        records = samples['repeat']['records']
        old = require_receipt(submit(catalog, objects, 'A', records))
        retire_volume(catalog, old.volume_id)
        task, = collect(catalog, objects)
        if task.state != 'awaiting_confirmation':
            raise AssertionError('expected the old deletion to wait for execution')
        first_b = submit(catalog, objects, 'B', records)
        if first_b.receipt is not None:
            before = read(catalog, objects, first_b.receipt.volume_id)
            if pickup_fields(before) != samples['repeat']:
                raise AssertionError('the first receipt did not provide the submitted records')
        other = require_receipt(submit(catalog, objects, 'D', samples['unrelated']['records']))
        other_pickup = read(catalog, objects, other.volume_id)
        emit('v6', samples['unrelated']['records'], samples['unrelated'], pickup_fields(other_pickup),
             'accepted', query_delete(objects, task.request_id).state,
             ['publish/submissions.py', 'objects/delete_queue.py', 'review/result.py'],
             'Zero and minus five give two records with peak five while the old delete is queued.')

        execute_delete(objects, task.request_id)
        catalog = initialize_catalog(catalog_path)
        objects = initialize_object_store(object_path)
        completion, = collect(catalog, objects)
        new = require_receipt(submit(catalog, objects, 'B', records))
        replay = require_receipt(submit(catalog, objects, 'B', records))
        final = read(catalog, objects, replay.volume_id)
        with objects.transaction() as connection:
            still_present = connection.execute(
                'SELECT 1 FROM payloads WHERE object_name = ?', (task.target,)
            ).fetchone() is not None
        old_retired = False
        try:
            read(catalog, objects, old.volume_id)
        except RetiredVolumeError:
            old_retired = True
        emit('v8', records, samples['repeat'], pickup_fields(final),
             {'old_object_present': False, 'old_volume_retired': True,
              'original_delete_completed': True, 'same_receipt': True},
             {'old_object_present': still_present, 'old_volume_retired': old_retired,
              'original_delete_completed': completion.request_id == task.request_id and completion.state == 'completed',
              'same_receipt': new == replay and (first_b.receipt is None or first_b.receipt == new)},
             ['objects/execution.py', 'retire/reconcile.py', 'publish/submissions.py', 'review/result.py'],
             'Three and minus eight give two records with peak eight. Compare the original request and both replays.')


if __name__ == '__main__':
    main()
