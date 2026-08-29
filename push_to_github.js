const git = require('isomorphic-git');
const http = require('isomorphic-git/http/node');
const fs = require('fs');
const path = require('path');

const dir = __dirname;
const token = process.argv[2] || process.env.GITHUB_TOKEN || process.env.GH_TOKEN;

async function push() {
    if (!token) {
        console.log("==================================================");
        console.log("GitHub Token Required to Push");
        console.log("Usage: node push_to_github.js <YOUR_GITHUB_TOKEN>");
        console.log("Or set GITHUB_TOKEN environment variable.");
        console.log("Generate token at: https://github.com/settings/tokens (with 'repo' scope)");
        console.log("==================================================");
        process.exit(1);
    }

    console.log("Pushing latest commits to origin/main...");
    try {
        const result = await git.push({
            fs,
            http,
            dir,
            remote: 'origin',
            ref: 'main',
            force: false,
            onAuth: () => ({
                username: token,
                password: ''
            })
        });
        console.log("✓ Successfully pushed to GitHub!", result);
    } catch (err) {
        console.error("Push failed:", err.message || err);
    }
}

push();
