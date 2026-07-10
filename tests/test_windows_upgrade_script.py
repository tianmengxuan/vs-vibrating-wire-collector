import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PS1 = ROOT / 'windows_upgrade' / 'upgrade_vs_collector.ps1'
CMD = ROOT / 'windows_upgrade' / 'upgrade_vs_collector.cmd'
README = ROOT / 'windows_upgrade' / 'README_升级说明.md'
WORKFLOW = ROOT / '.github' / 'workflows' / 'build.yml'
GITIGNORE = ROOT / '.gitignore'


class WindowsUpgradeScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ps1_bytes = PS1.read_bytes()
        cls.cmd_bytes = CMD.read_bytes()
        cls.ps1_text = cls.ps1_bytes.decode('ascii')
        cls.cmd_text = cls.cmd_bytes.decode('ascii')
        cls.readme_text = README.read_text(encoding='utf-8')
        cls.workflow_text = WORKFLOW.read_text(encoding='utf-8')
        cls.gitignore_text = GITIGNORE.read_text(encoding='utf-8')

    def test_execution_scripts_are_strictly_ascii(self):
        self.assertTrue(self.ps1_bytes.isascii())
        self.assertTrue(self.cmd_bytes.isascii())
        self.assertFalse(self.ps1_bytes.startswith(b'\xef\xbb\xbf'))
        self.assertNotIn('chcp', self.cmd_text.lower())

    def test_installed_exe_name_is_constructed_from_code_points(self):
        code_points_match = re.search(
            r'\$codePoints\s*=\s*@\(([^)]*)\)',
            self.ps1_text,
        )
        self.assertIsNotNone(code_points_match)
        code_points = [
            int(value.strip(), 16)
            for value in code_points_match.group(1).split(',')
        ]
        self.assertEqual('VS' + ''.join(map(chr, code_points)) + '.exe', 'VS振弦数据采集器.exe')
        self.assertIn("return 'VS' + (-join $characters) + '.exe'", self.ps1_text)

    def test_payload_name_is_ascii_and_distinct(self):
        payload_name = re.search(r"\$payloadExeName\s*=\s*'([^']+)'", self.ps1_text).group(1)
        self.assertEqual(payload_name, 'VSCollector_Update.exe')
        self.assertTrue(payload_name.isascii())
        self.assertIn('$payloadExeName -ieq $appExeName', self.ps1_text)

    def test_cmd_redirects_powershell_and_preserves_exit_code(self):
        self.assertIn('set "LOG_FILE=%~dp0upgrade_vs_collector.log"', self.cmd_text)
        self.assertIn('-File "%SCRIPT_FILE%" >nul 2>&1', self.cmd_text)
        self.assertIn('set "EXIT_CODE=%ERRORLEVEL%"', self.cmd_text)
        self.assertIn('exit /b %EXIT_CODE%', self.cmd_text)
        self.assertNotIn('pause', self.cmd_text.lower())

    def test_console_output_is_fixed_ascii_without_paths_or_errors(self):
        self.assertNotIn('Write-Host', self.ps1_text)
        self.assertNotIn('Write-Error', self.ps1_text)
        self.assertNotIn('Write-Output', self.ps1_text)
        self.assertNotIn('$_.Exception', self.ps1_text)
        self.assertNotRegex(self.ps1_text, r'Write-(?:Output|Status)[^\n]*\$scriptDir')
        self.assertNotRegex(self.ps1_text, r'Write-(?:Output|Status)[^\n]*ExePath')
        self.assertNotRegex(self.ps1_text, r'Write-(?:Output|Status)[^\n]*ExeName')

        status_calls = re.findall(r"Write-Status\s+'([^']*)'", self.ps1_text)
        self.assertGreater(len(status_calls), 0)
        self.assertTrue(all(message.isascii() for message in status_calls))
        self.assertNotRegex(self.ps1_text, r'Write-Status\s+"')
        self.assertIn('[System.IO.File]::AppendAllText', self.ps1_text)
        self.assertIn('[System.Text.Encoding]::ASCII', self.ps1_text)

        echo_lines = re.findall(r'(?im)^\s*echo\s+(.+)$', self.cmd_text)
        self.assertGreater(len(echo_lines), 0)
        self.assertTrue(all(message.isascii() for message in echo_lines))
        self.assertNotRegex(self.cmd_text, r'(?im)^\s*echo[^\n]*%~dp0')

    def test_script_uses_its_own_directory_and_literal_paths(self):
        self.assertIn('$PSScriptRoot', self.ps1_text)
        self.assertIn('Set-Location -LiteralPath $scriptDir', self.ps1_text)
        self.assertIn('Join-Path $scriptDir $appExeName', self.ps1_text)
        self.assertIn('set "SCRIPT_FILE=%~dp0upgrade_vs_collector.ps1"', self.cmd_text)

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

        self.assertIn(
            'Copy-IfExists -SourcePath (Join-Path $scriptDir $configFileName) -BackupDir $backupDir',
            self.ps1_text,
        )
        forbidden_config_mutation = (
            r'(?im)^\s*(?:Set-Content|Out-File|Add-Content|Clear-Content|Move-Item|'
            r'Remove-Item|Rename-Item)\b[^\n]*(?:config\.env|sites\.json|formulas\.json|'
            r'\$configFileName|\$configFileNames)'
        )
        self.assertNotRegex(self.ps1_text, forbidden_config_mutation)

    def test_running_program_is_detected_without_force_kill(self):
        self.assertIn('Get-CimInstance Win32_Process', self.ps1_text)
        self.assertIn('Get-Process -Name', self.ps1_text)
        self.assertIn('return 2', self.ps1_text)
        self.assertNotRegex(self.ps1_text + self.cmd_text, r'\bStop-Process\b|\btaskkill\b')

    def test_replacement_has_backup_rollback_and_start(self):
        self.assertIn('Copy-Item -LiteralPath $currentExePath -Destination $backupExePath -Force', self.ps1_text)
        self.assertIn('$backupExePath = Join-Path $backupDir $appExeName', self.ps1_text)
        self.assertIn('Move-Item -LiteralPath $currentExePath -Destination $tempOldExe -Force', self.ps1_text)
        self.assertIn('Copy-Item -LiteralPath $payloadExePath -Destination $currentExePath -Force', self.ps1_text)
        self.assertIn('Restore-OldExe -TempOldExe $tempOldExe', self.ps1_text)
        self.assertIn('Move-Item -LiteralPath $TempOldExe -Destination $CurrentExePath -Force', self.ps1_text)
        self.assertIn('Copy-Item -LiteralPath $BackupExePath -Destination $CurrentExePath -Force', self.ps1_text)
        self.assertNotRegex(self.ps1_text, r'Remove-Item\s+-LiteralPath\s+\$backupExePath\b')
        self.assertNotRegex(self.ps1_text, r'Remove-Item\s+-LiteralPath\s+\$backupDir\b')

        start_index = self.ps1_text.index('Start-Process -FilePath $currentExePath -WorkingDirectory $scriptDir')
        temp_remove_index = self.ps1_text.index(
            'Remove-Item -LiteralPath $tempOldExe -Force -ErrorAction SilentlyContinue'
        )
        self.assertLess(start_index, temp_remove_index)

    def test_payload_is_required_nonzero_and_not_a_config_file(self):
        self.assertIn('Join-Path $scriptDir $payloadExeName', self.ps1_text)
        self.assertIn('Test-Path -LiteralPath $payloadExePath -PathType Leaf', self.ps1_text)
        self.assertIn('(Get-Item -LiteralPath $payloadExePath).Length -le 0', self.ps1_text)
        config_assignment = re.search(r"\$configFileNames\s*=\s*@\(([^)]*)\)", self.ps1_text)
        self.assertNotIn('VSCollector_Update.exe', config_assignment.group(1))

    def test_workflow_smokes_cmd_and_ascii_package_surface(self):
        self.assertIn("& $env:ComSpec /d /c 'call upgrade_vs_collector.cmd'", self.workflow_text)
        self.assertIn('if ($cmdExitCode -ne 0)', self.workflow_text)
        self.assertIn("$payloadExeName = 'VSCollector_Update.exe'", self.workflow_text)
        self.assertIn("Test-AsciiFile -Path (Join-Path $smokeDir 'upgrade_vs_collector.cmd')", self.workflow_text)
        self.assertIn("Test-AsciiFile -Path (Join-Path $smokeDir 'upgrade_vs_collector.ps1')", self.workflow_text)
        self.assertIn("$entry -notmatch '^[\\x00-\\x7F]+$'", self.workflow_text)

    def test_readme_documents_console_fix_and_ascii_payload(self):
        self.assertIn('0x1F', self.readme_text)
        self.assertIn('乱码', self.readme_text)
        self.assertIn('Windows PowerShell 5.1', self.readme_text)
        self.assertIn('ASCII', self.readme_text)
        self.assertIn('VSCollector_Update.exe', self.readme_text)
        self.assertIn('upgrade_backup_yyyyMMdd_HHmmss', self.readme_text)
        self.assertIn('成功后也会保留', self.readme_text)

    def test_field_json_configs_are_gitignored_without_touching_config_env_rule(self):
        self.assertRegex(self.gitignore_text, r'(?m)^config\.env$')
        self.assertRegex(self.gitignore_text, r'(?m)^sites\.json$')
        self.assertRegex(self.gitignore_text, r'(?m)^formulas\.json$')


if __name__ == '__main__':
    unittest.main()
