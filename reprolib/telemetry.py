"""Live NVML core/HBM sensors and host counters; no dependency on a cooling app."""
import ctypes as C
import datetime
import json
import os
from pathlib import Path
import time

import psutil


class Memory(C.Structure):
    _fields_ = [('total', C.c_ulonglong), ('free', C.c_ulonglong), ('used', C.c_ulonglong)]


class Utilization(C.Structure):
    _fields_ = [('gpu', C.c_uint), ('memory', C.c_uint)]


class FieldValue(C.Structure):
    _fields_ = [('field_id', C.c_uint), ('scope_id', C.c_uint), ('timestamp_us', C.c_longlong),
                ('latency_us', C.c_longlong), ('value_type', C.c_int), ('status', C.c_int),
                ('value', C.c_ulonglong)]


class Sampler:
    def __init__(self):
        self.error = None
        self.devices = []
        try:
            self.dll = C.CDLL(str(Path(os.environ['SystemRoot']) / 'System32/nvml.dll'))
            if self.dll.nvmlInit_v2() != 0:
                raise RuntimeError('nvmlInit_v2 failed')
            count = C.c_uint()
            if self.dll.nvmlDeviceGetCount_v2(C.byref(count)) != 0:
                raise RuntimeError('NVML GPU enumeration failed')
            for index in range(count.value):
                handle = C.c_void_p()
                if self.dll.nvmlDeviceGetHandleByIndex_v2(C.c_uint(index), C.byref(handle)) != 0:
                    continue
                identity = {'index': index}
                for function, label in [('nvmlDeviceGetName', 'name'), ('nvmlDeviceGetUUID', 'uuid')]:
                    buffer = C.create_string_buffer(128)
                    if getattr(self.dll, function)(handle, buffer, C.c_uint(128)) == 0:
                        identity[label] = buffer.value.decode('utf-8')
                self.devices.append((handle, identity))
        except (OSError, KeyError, RuntimeError) as error:
            self.error = str(error)

    def sample(self, phase):
        now = time.time()
        data = {'time': now, 'phase': phase, 'available_ram_bytes': psutil.virtual_memory().available,
                'host_disk_reads_bytes': psutil.disk_io_counters().read_bytes, 'gpus': []}
        if self.error:
            data['nvml_error'] = self.error
        for handle, identity in self.devices:
            gpu = {**identity, 'source': 'live NVML', 'observed_time': now}
            for function, label, extra, divisor in [
                ('nvmlDeviceGetTemperature', 'temperature_c', [C.c_uint(0)], 1),
                ('nvmlDeviceGetPowerUsage', 'power_w', [], 1000),
                ('nvmlDeviceGetPcieThroughput', 'pcie_rx_kb_s', [C.c_uint(1)], 1),
                ('nvmlDeviceGetPcieThroughput', 'pcie_tx_kb_s', [C.c_uint(0)], 1),
            ]:
                value = C.c_uint()
                status = getattr(self.dll, function)(handle, *extra, C.byref(value))
                if status == 0:
                    gpu[label] = value.value / divisor
            util = Utilization()
            if self.dll.nvmlDeviceGetUtilizationRates(handle, C.byref(util)) == 0:
                gpu.update(utilization_percent=util.gpu, memory_utilization_percent=util.memory)
            memory = Memory()
            if self.dll.nvmlDeviceGetMemoryInfo(handle, C.byref(memory)) == 0:
                gpu.update(memory_used_bytes=memory.used, memory_free_bytes=memory.free,
                           memory_total_bytes=memory.total)
            if 'V100' in gpu.get('name', ''):
                field = FieldValue(field_id=82)
                if (self.dll.nvmlDeviceGetFieldValues(handle, C.c_int(1), C.byref(field)) == 0
                        and field.status == 0 and field.value_type == 1 and 1 <= field.value <= 125
                        and abs(now - field.timestamp_us / 1e6) <= 8):
                    gpu.update(memory_temperature_c=field.value,
                               memory_temperature_observed_time=field.timestamp_us / 1e6)
            data['gpus'].append(gpu)
        return data


if __name__ == '__main__':
    print(json.dumps(Sampler().sample('probe'), indent=2, ensure_ascii=False))
