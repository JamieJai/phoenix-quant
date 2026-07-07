from pathlib import Path
from datetime import datetime

TARGET = Path("phoenix_core/services/telegram_command_bot.py")

HELPER = '\ndef _filter_stderr(stderr: str) -> str:\n    if not stderr:\n        return ""\n\n    keep = []\n    noisy_keywords = [\n        "INFO phoenix.data_loader",\n        "다운로드 완료",\n    ]\n\n    for line in stderr.splitlines():\n        stripped = line.strip()\n        if not stripped:\n            continue\n\n        if any(k in stripped for k in noisy_keywords):\n            continue\n\n        keep.append(line)\n\n    return "\\n".join(keep).strip()\n\n\ndef _compact_analyze_output(text: str) -> str:\n    """\n    긴 Phoenix CLI 리포트를 Telegram용 핵심 요약으로 줄인다.\n    PHOENIX_ANALYZE_COMPACT=0 으로 끌 수 있다.\n    """\n    if not text:\n        return "(출력 없음)"\n\n    lines = [line.rstrip() for line in text.splitlines()]\n    picked = []\n\n    prefixes = (\n        "Phoenix Quant",\n        "Ticker:",\n        "기준일:",\n        "기준가:",\n        "단타 적합도:",\n        "신뢰도:",\n        "위험도:",\n        "시장 우호도:",\n        "업종 ETF:",\n        "Pattern Rarity:",\n        "유지 점수:",\n        "VIX:",\n        "과거 유사 사례:",\n        "5거래일 +5% 도달률:",\n        "10거래일 +10% 도달률:",\n    )\n\n    in_market = False\n    in_sector = False\n    in_decision = False\n    summary_line = None\n    report_line = None\n\n    for raw in lines:\n        line = raw.strip()\n\n        if not line:\n            continue\n\n        if line.startswith("[") and "]" in line:\n            # [1/6] 같은 진행 로그는 제거\n            continue\n\n        if line.startswith(prefixes):\n            picked.append(line)\n            continue\n\n        if line.startswith("Market Regime:"):\n            picked.append("")\n            picked.append("Market Regime:")\n            in_market = True\n            in_sector = in_decision = False\n            continue\n\n        if line.startswith("Sector Rotation:"):\n            picked.append("")\n            picked.append("Sector Rotation:")\n            in_sector = True\n            in_market = in_decision = False\n            continue\n\n        if line.startswith("Decision Breakdown:"):\n            picked.append("")\n            picked.append("Decision Breakdown:")\n            in_decision = True\n            in_market = in_sector = False\n            continue\n\n        if line.startswith("Confidence Breakdown:"):\n            in_market = in_sector = in_decision = False\n            continue\n\n        if in_market and (\n            line.startswith("- Regime:")\n            or line.startswith("- Momentum:")\n        ):\n            picked.append("  " + line)\n            continue\n\n        if in_sector and (\n            line.startswith("- Target:")\n            or line.startswith("- Top Sectors:")\n        ):\n            picked.append("  " + line)\n            continue\n\n        if in_decision and (\n            "최종" in line\n            or line.startswith("- pattern_contribution")\n            or line.startswith("- market_contribution")\n            or line.startswith("- sector_adjustment")\n            or line.startswith("- risk_penalty")\n        ):\n            picked.append("  " + line)\n            continue\n\n        if line.startswith("AI Summary:"):\n            summary_line = line\n            continue\n\n        if line.startswith("(리포트 저장됨:"):\n            report_line = line\n            continue\n\n    if summary_line:\n        picked.append("")\n        if len(summary_line) > 650:\n            summary_line = summary_line[:650].rstrip() + "..."\n        picked.append(summary_line)\n\n    if report_line:\n        picked.append("")\n        picked.append(report_line)\n\n    result = "\\n".join(picked).strip()\n    return result if result else text\n'

def main():
    if not TARGET.exists():
        raise SystemExit(f"대상 파일 없음: {TARGET}")

    s = TARGET.read_text(encoding="utf-8")

    backup = TARGET.with_suffix(TARGET.suffix + f".bak_compact_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    backup.write_text(s, encoding="utf-8")

    if "def _compact_analyze_output(" not in s:
        marker = "\ndef _env_bool("
        if marker not in s:
            raise SystemExit("패치 실패: def _env_bool 위치를 찾지 못했습니다.")
        s = s.replace(marker, HELPER + marker, 1)

    old = """        out = self._run_phoenix(cmd)

        extra = ""
        if self.intraday_enabled:
            ctx = self.intraday_engine.analyze(ticker)
            extra = "\\n\\n" + format_intraday_context(ctx)

        return f"{header(f'{ticker} 상세 분석')}\\n\\n{out}{extra}\\n\\n{disclaimer()}"
"""

    new = """        out = self._run_phoenix(cmd)
        daily_out = _compact_analyze_output(out) if _env_bool("PHOENIX_ANALYZE_COMPACT", True) else out

        extra = ""
        if self.intraday_enabled:
            ctx = self.intraday_engine.analyze(ticker)
            extra = "\\n\\n" + format_intraday_context(ctx)

        return f"{header(f'{ticker} 상세 분석')}\\n\\n{daily_out}{extra}\\n\\n{disclaimer()}"
"""

    if old in s:
        s = s.replace(old, new, 1)
    elif "daily_out = _compact_analyze_output(out)" not in s:
        raise SystemExit("패치 실패: _cmd_analyze 블록을 찾지 못했습니다. 파일이 예상과 다릅니다.")

    old_stderr = """        if proc.stderr:
            output += "\\n[stderr]\\n" + proc.stderr
"""
    new_stderr = """        stderr = _filter_stderr(proc.stderr)
        if stderr:
            output += "\\n[stderr]\\n" + stderr
"""
    if old_stderr in s:
        s = s.replace(old_stderr, new_stderr, 1)

    TARGET.write_text(s, encoding="utf-8")

    print("패치 완료")
    print(f"백업 파일: {backup}")
    print("적용 내용:")
    print("- /analyze 출력 compact 요약 적용")
    print("- PHOENIX_ANALYZE_COMPACT=0 으로 긴 원문 출력 복구 가능")
    print("- INFO성 stderr 로그 숨김")

if __name__ == "__main__":
    main()
