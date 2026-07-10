import codecs
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PS1 = ROOT / 'windows_upgrade' / 'upgrade_vs_collector.ps1'
CMD = ROOT / 'windows_upgrade' / 'upgrade_vs_collector.cmd'
README = ROOT / 'windows_upgrade' / 'README_升级说明.md'
GITIGNORE = ROOT / '.gitignore'


class WindowsUpgradeScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ps1_bytes = PS1.read_bytes()
        cls.ps1_text = cls.ps1_bytes.decode('utf-8-sig')
        cls.cmd_text = CMD.read_text(encoding='utf-8')
        cls.readme_text = README.read_text(encoding='utf-8')
        cls.gitignore_text = GITIGNORE.read_text(encoding='utf-8')

    def test_ps1_is_utf8_bom_for_windows_powershell_51(self):
        self.assertTrue(
            self.ps1_bytes.startswith(codecs.BOM_UTF8),
            'Windows PowerShell 5.1 needs UTF-8 BOM for Chinese EXE names',
        )
        self.assertIn('VS振弦数据采集器.exe', self.ps1_text)
        self.assertIn('VS振弦数据采集器_新版.exe', self.ps1_text)

    def test_payload_name_is_distinct_from_current_exe(self):
        app_name = re.search(r"\$appExeName\s*=\s*'([^']+)'", self.ps1_text).group(1)
        payload_name = re.search(r"\$payloadExeName\s*=\s*'([^']+)'", self.ps1_text).group(1)

        self.assertEqual(app_name, 'VS振弦数据采集器.exe')
        self.assertNotEqual(payload_name, app_name)
        self.assertTrue(payload_name.endswith('_新版.exe'))
        self.assertIn('$payloadExeName -ieq $appExeName', self.ps1_text)

    def test_script_uses_its_own_directory_and_literal_paths(self):
        self.assertIn('$PSScriptRoot', self.ps1_text)
        self.assertIn('Set-Location -LiteralPath $scriptDir', self.ps1_text)
        self.assertIn('Join-Path $scriptDir $appExeName', self.ps1_text)
        self.assertIn('cd /d "%~dp0"', self.cmd_text)
        self.assertIn('-File "%~dp0upgrade_vs_collector.ps1"', self.cmd_text)

    def test_config_files_are_backed_up_but_not_modified(self):
        config_assignment = re.search(r"\$configFileNames\s*=\s*@\(([^)]*)\)", self.ps1_text)
        self.assertIsNotNone(config_assignment)
        self.assertEqual(
            re.findall(r"'([^']+)'", config_assignment.group(1)),
            ['config.env', 'sites.json', 'formulas.json'],
        )

        for name in ('config.env', 'sites.json', 'formulas.json'):
            with self.subTest(name=name):
                self.assertIn(name, self.ps1_text)
                self.assertIn(name, self.readme_text)

        self.assertIn('Copy-IfExists -SourcePath', self.ps1_text)
        self.assertIn(
            'Copy-IfExists -SourcePath (Join-Path $scriptDir $configFileName) -BackupDir $backupDir',
            self.ps1_text,
        )
        forbidden_config_mutation = (
            r'(?im)^\s*(?:Set-Content|Out-File|Add-Content|Clear-Content|Move-Item|'
            r'Remove-Item|Rename-Item)\b[^\n]*(?:config\.env|sites\.json|formulas\.json|'
            r'\$configFileName|\$configFileNames)'
        )
        self.assertNotRegex(
            self.ps1_text,
            forbidden_config_mutation,
        )

    def test_running_program_is_detected_without_force_kill(self):
        self.assertIn('Get-CimInstance Win32_Process', self.ps1_text)
        self.assertIn('Get-Process -Name', self.ps1_text)
        self.assertIn('exit 2', self.ps1_text)
        self.assertNotRegex(self.ps1_text + self.cmd_text, r'\bStop-Process\b|\btaskkill\b')

    def test_replacement_has_rollback_and_starts_new_exe(self):
        self.assertIn('Copy-Item -LiteralPath $currentExePath -Destination $backupExePath -Force', self.ps1_text)
        self.assertIn('$backupExePath = Join-Path $backupDir $appExeName', self.ps1_text)
        self.assertIn('Move-Item -LiteralPath $currentExePath -Destination $tempOldExe -Force', self.ps1_text)
        self.assertIn('Copy-Item -LiteralPath $payloadExePath -Destination $currentExePath -Force', self.ps1_text)
        self.assertIn('Restore-OldExe -TempOldExe $tempOldExe', self.ps1_text)
        self.assertIn('Move-Item -LiteralPath $TempOldExe -Destination $CurrentExePath -Force', self.ps1_text)
        self.assertIn('Copy-Item -LiteralPath $BackupExePath -Destination $CurrentExePath -Force', self.ps1_text)
        self.assertIn('Remove-Item -LiteralPath $tempOldExe -Force -ErrorAction SilentlyContinue', self.ps1_text)
        self.assertNotRegex(self.ps1_text, r'Remove-Item\s+-LiteralPath\s+\$backupExePath\b')
        self.assertNotRegex(self.ps1_text, r'Remove-Item\s+-LiteralPath\s+\$backupDir\b')
        self.assertIn('Start-Process -FilePath $currentExePath -WorkingDirectory $scriptDir', self.ps1_text)

    def test_payload_is_required_nonzero_and_not_a_config_file(self):
        self.assertIn('Join-Path $scriptDir $payloadExeName', self.ps1_text)
        self.assertIn('Test-Path -LiteralPath $payloadExePath -PathType Leaf', self.ps1_text)
        self.assertIn('(Get-Item -LiteralPath $payloadExePath).Length -le 0', self.ps1_text)
        config_assignment = re.search(r"\$configFileNames\s*=\s*@\(([^)]*)\)", self.ps1_text)
        self.assertNotIn('VS振弦数据采集器_新版.exe', config_assignment.group(1))

    def test_chinese_and_space_paths_use_literal_path(self):
        self.assertIn('[System.IO.Path]::GetFullPath($scriptDir)', self.ps1_text)
        self.assertIn('Set-Location -LiteralPath $scriptDir', self.ps1_text)
        self.assertIn('Test-Path -LiteralPath $currentExePath -PathType Leaf', self.ps1_text)
        self.assertIn('Test-Path -LiteralPath $payloadExePath -PathType Leaf', self.ps1_text)
        self.assertIn('Copy-Item -LiteralPath $payloadExePath -Destination $currentExePath -Force', self.ps1_text)
        self.assertIn('Start-Process -FilePath $currentExePath -WorkingDirectory $scriptDir', self.ps1_text)

    def test_readme_describes_package_without_config_files(self):
        self.assertIn('升级包不要包含服务器上的 `config.env`、`sites.json`、`formulas.json`', self.readme_text)
        self.assertIn('VS振弦数据采集器_新版.exe', self.readme_text)
        self.assertIn('upgrade_backup_yyyyMMdd_HHmmss', self.readme_text)
        self.assertIn('Windows PowerShell 5.1', self.readme_text)
        self.assertIn('成功后也会保留', self.readme_text)

    def test_field_json_configs_are_gitignored_without_touching_config_env_rule(self):
        self.assertRegex(self.gitignore_text, r'(?m)^config\.env$')
        self.assertRegex(self.gitignore_text, r'(?m)^sites\.json$')
        self.assertRegex(self.gitignore_text, r'(?m)^formulas\.json$')


if __name__ == '__main__':
    unittest.main()
