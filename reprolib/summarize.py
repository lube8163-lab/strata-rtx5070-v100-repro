"""Aggregate preserved engine metrics; never estimate token counts from characters."""
from pathlib import Path
import json
import os
import re
import statistics as S

import sys
ROOT = Path(sys.argv[1]).resolve()
GIB = 2**30


def median(values):
    values = [v for v in values if v is not None]
    return S.median(values) if values else None


def aggregate(name):
    folder = ROOT/name
    r = json.loads((folder/'results.json').read_text(encoding='utf-8'))
    samples = [json.loads(line) for line in (folder/'telemetry.jsonl').read_text(encoding='utf-8').splitlines()]
    requests = r['requests']
    speed = [x for x in requests if x['group']=='speed']
    quality = [x for x in requests if x['group']=='quality']
    for x in quality:
        x['content_passed']=x['grade']['passed']
        if not x['content_passed'] and 'expected' in x['grade'] and x['answer']['finish']!='length':
            candidate=x['answer']['text'].strip()
            match=re.fullmatch(r'```(?:json)?\s*\n(.*?)\n?```',candidate,re.S)
            if match:
                try:
                    actual=json.loads(match.group(1))
                    x['content_passed']=actual==x['grade']['expected']
                except ValueError:
                    pass
    active = [x for x in samples if x['phase'].startswith(('speed:','quality:','review:','long:'))]
    speed_samples = [x for x in samples if x['phase'].startswith('speed:')]
    baseline = [x for x in samples if x['phase']=='baseline']
    idle = [x for x in samples if x['phase']=='ready_idle']
    def cpu(rows):
        return dict(host_mean_percent=S.mean(x['host_cpu_percent'] for x in rows) if rows else None,
                    owned_mean_percent_of_pc=S.mean(sum(p['cpu_percent_one_core'] for p in x['owned_processes'])/r.get('logical_cpu_count',os.cpu_count()) for x in rows) if rows else None)
    def resources(rows):
        result = dict(samples=len(rows), **cpu(rows))
        if not rows:
            return result
        result.update(available_ram_min_gib=min(x['available_ram_bytes'] for x in rows)/GIB,
                      available_ram_median_gib=median(x['available_ram_bytes'] for x in rows)/GIB,
                      owned_rss_peak_gib=max(sum(p['rss_bytes'] for p in x['owned_processes']) for x in rows)/GIB,
                      owned_private_peak_gib=max(sum(p['private_bytes'] or 0 for p in x['owned_processes']) for x in rows)/GIB)
        result['gpus']={}
        for index in (0,1):
            gs=[g for x in rows for g in x['gpus'] if g['index']==index]
            result['gpus'][str(index)]=dict(name=gs[0]['name'],
                used_peak_gib=max(g['memory_used_bytes'] for g in gs)/GIB,
                used_median_gib=median(g['memory_used_bytes'] for g in gs)/GIB,
                free_min_gib=min(g['memory_free_bytes'] for g in gs)/GIB,
                utilization_mean_percent=S.mean(g['utilization_percent'] for g in gs),
                utilization_peak_percent=max(g['utilization_percent'] for g in gs),
                power_mean_w=S.mean(g['power_w'] for g in gs), power_peak_w=max(g['power_w'] for g in gs),
                temperature_peak_c=max(g.get('temperature_c',0) for g in gs),
                hbm_temperature_peak_c=max(g.get('memory_temperature_c',0) for g in gs),
                pcie_rx_mean_kb_s=S.mean(g.get('pcie_rx_kb_s',0) for g in gs),
                pcie_tx_mean_kb_s=S.mean(g.get('pcie_tx_kb_s',0) for g in gs))
        return result
    summary=dict(completed=r.get('completed'), error=r.get('error'), guard_stop=r.get('guard_stop'),
                 load_seconds=r.get('load_seconds'), fixture_sha256=r['fixture_sha256'],
                 scored_total=len(quality), scored_passed=sum(x['grade']['passed'] for x in quality),
                 content_passed=sum(x['content_passed'] for x in quality),
                 format_only_failures=[x['name'] for x in quality if x['content_passed'] and not x['grade']['passed']],
                 failures=[dict(name=x['name'],text=x['answer']['text'],grade=x['grade'],finish=x['answer']['finish']) for x in quality if not x['grade']['passed']],
                 baseline=resources(baseline), loaded_idle=resources(idle),
                 active=resources(active), speed_resources=resources(speed_samples),
                 all_phases_min_available_ram_gib=min(x['available_ram_bytes'] for x in samples)/GIB,
                 all_phases_peak_core_c=max(g.get('temperature_c',0) for x in samples for g in x['gpus']),
                 all_phases_peak_hbm_c=max(g.get('memory_temperature_c',0) for x in samples for g in x['gpus']),
                 logical_cpu_count=r.get('logical_cpu_count',os.cpu_count()))
    summary['speed']={}
    for task in ('japanese','python'):
        rows=[x for x in speed if x['name']==task]
        summary['speed'][task]=dict(n=len(rows), decode_tps_median=median(x['decode_tps'] for x in rows),
            ttft_s_median=median(x['ttft_s'] for x in rows), total_s_median=median(x['total_s'] for x in rows),
            tokens=[x['generated_tokens'] for x in rows], finishes=[x['answer']['finish'] for x in rows],
            characters_per_second_median=median(x['character_count']/x['total_s'] for x in rows),
            host_disk_read_median_gib=median(x['host_disk_reads_bytes_delta'] for x in rows)/GIB if rows else None,
            reused_tokens=[x['reused_tokens'] for x in rows],
            drafts_offered=sum(x['drafts_offered'] or 0 for x in rows),
            drafts_accepted=sum(x['drafts_accepted'] or 0 for x in rows))
    summary['speed_mixed_decode_tps_median']=median(x['decode_tps'] for x in speed)
    summary['speed_mixed_ttft_s_median']=median(x['ttft_s'] for x in speed)
    summary['speed_disk_read_median_gib']=median(x['host_disk_reads_bytes_delta'] for x in speed)/GIB if speed else None
    summary['quality_elapsed_total_s']=sum(x['total_s'] for x in quality)
    summary['quality_by_category']={}
    for category in sorted({x['category'] for x in quality}):
        rows=[x for x in quality if x['category']==category]
        summary['quality_by_category'][category]=dict(passed=sum(x['grade']['passed'] for x in rows),total=len(rows))
    summary['long']=[dict(name=x['name'],prompt_tokens=x['prompt_tokens'],read_tokens=x['read_tokens'],reused_tokens=x['reused_tokens'],
                          prompt_tps=x['prompt_tps'],ttft_s=x['ttft_s'],total_s=x['total_s'],passed=x.get('grade',{}).get('passed'),
                          text=x['answer']['text'],host_disk_read_gib=x['host_disk_reads_bytes_delta']/GIB) for x in requests if x['group']=='long']
    summary['review']=[dict(name=x['name'],characters=x['character_count'],total_s=x['total_s'],decode_tps=x['decode_tps'],
                            finish=x['answer']['finish'],text=x['answer']['text']) for x in requests if x['group']=='review']
    return summary,requests


if __name__=='__main__':
    summary={}
    answers=['# 同一課題の回答と採点\n\nThinking OFF、temperature 0。24問の固定採点と日本語回答の保存。\n']
    for name in ('rana','flash','rana-thinking'):
        if not (ROOT/name/'results.json').exists():
            continue
        summary[name],requests=aggregate(name)
        answers.append(f'## {name}\n')
        for x in requests:
            if x['group'] not in ('quality','review','long','thinking'):
                continue
            answers.append(f'### {x["group"]}: {x["name"]}\n\n問い:\n\n{x["prompt"]}\n\n回答:\n\n{x["answer"]["text"]}\n')
            if x.get('grade'):
                answers.append('\n採点: '+json.dumps(x['grade'],ensure_ascii=False)+'\n')
        print(name,summary[name]['completed'],summary[name]['scored_passed'],summary[name]['scored_total'])
    if 'rana' in summary and 'flash' in summary:
        a=[x for x in json.loads((ROOT/'rana/results.json').read_text(encoding='utf-8'))['requests'] if x['group']!='thinking']
        b=[x for x in json.loads((ROOT/'flash/results.json').read_text(encoding='utf-8'))['requests'] if x['group']!='thinking']
        summary['matched_prompts']=len(a)==len(b) and all(x['prompt_sha256']==y['prompt_sha256'] for x,y in zip(a,b))
        summary['matched_fixture']=summary['rana']['fixture_sha256']==summary['flash']['fixture_sha256']
    (ROOT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'answers.md').write_text('\n'.join(answers),encoding='utf-8')
