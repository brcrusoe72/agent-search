Output "docs/demo/demo.gif"
Set Width 1000
Set Height 600
Set Theme "Monokai"
Set Shell "bash"
Set TypingSpeed 0ms

Type "docker compose up -d"
Enter
Sleep 2s

Type "curl -s \"http://localhost:3939/search?q=python+async+patterns&count=3\" | jq ."
Enter
Sleep 3s
