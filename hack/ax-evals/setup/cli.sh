# Build the daytona CLI from the checked-out source onto PATH.
install_go
t "build cli"
cd /opt/clients
retry env GOWORK=/opt/clients/go.work go build -o /usr/local/bin/daytona ./cli
daytona --help | head -n 3
t done
