# 内置刷机工具（不随仓库提交）

晶晨的刷机工具是闭源软件，仓库里不提交它的二进制。

获取方式：

1. 初始化页点「下载工具」，打开[官方页面](https://androidmtk.com/download-amlogic-flash-tool)；
2. 下载 `Amlogic_Flash_Tool_v6.0.0` 的 zip；
3. 把 zip 放进本目录（不需要手动解压）；
4. 回到初始化页点「重新检测」。

支持两种形式：

```
tools/
├── Amlogic_Flash_Tool_v6.0.0.zip   # 自动解压
└── Amlogic_Flash_Tool_v6.0.0/      # 或已经解压好的目录
```

程序会递归查找 `update.exe`，刷机页自动填入。

> zip 和二进制文件已被 `.gitignore` 忽略，不会误提交。
