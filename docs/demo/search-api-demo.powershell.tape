Output "docs/demo/demo.gif"
Set Width 1000
Set Height 600
Set Theme "Monokai"
Set Shell "powershell"
Set TypingSpeed 0ms

Type "docker compose up -d"
Enter
Sleep 2s

Type "curl.exe -s \"http://localhost:3939/search?q=python+async+patterns&count=3\" | jq ."
Enter
Sleep 3s
