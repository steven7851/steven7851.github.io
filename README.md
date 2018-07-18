https://steven7851.github.io/signature.html?uid=63628384192

`node signature.js 63628384192`
```node.js
var request = require('request');
var uid = process.argv.splice(2);
var data = { uid: uid };

request({ url: 'https://steven7851.github.io/signature.html', qs: data }, function (err, response, body) {
    if (err) {
        console.log(err);return;
    }
    console.log(body);
});
```
