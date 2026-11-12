# 离线依赖 wheels

这里是从 PyPI 下载的第三方包，**未做任何修改**，只用于首次启动时离线安装
`pywebview` / `paramiko`。如果不需要离线安装，可以直接删除整个目录，初始化页会自动改用 PyPI 镜像。

| 包 | 许可证 |
|---|---|
| bcrypt | Apache-2.0 |
| bottle | MIT |
| cffi | MIT-0 |
| clr_loader | MIT |
| cryptography | Apache-2.0 OR BSD-3-Clause |
| invoke | BSD-2-Clause |
| packaging | Apache-2.0 OR BSD-2-Clause |
| paramiko | LGPL-2.1 |
| pycparser | BSD-3-Clause |
| PyNaCl | Apache-2.0 |
| pythonnet | MIT |
| pywebview | BSD-3-Clause |
| setuptools | MIT |
| typing_extensions | PSF-2.0 |
| wheel | MIT |
| proxy_tools | MIT |

每个 wheel 的 `*.dist-info/` 目录内都带有完整的 LICENSE 文件；源码和许可证见各项目主页：

- paramiko: <https://github.com/paramiko/paramiko>
- pywebview: <https://github.com/r0x0r/pywebview>
- pythonnet: <https://github.com/pythonnet/pythonnet>
- cryptography: <https://github.com/pyca/cryptography>
- 其余包可在 <https://pypi.org/> 搜索包名查看。

`proxy_tools-0.1.0.tar.gz` 是源码包，构建时会生成 wheel，内容同样未修改。
