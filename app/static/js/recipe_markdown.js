// Renders a recipe's markdown notes into HTML (used by recipe_list.html's per-recipe
// view). Lives in its own file, loaded once per page, rather than inline in recipe_list.html
// itself -- that fragment is included multiple times on the combined Library page
// (legacy_recipes.html), and top-level const/let declarations can't be repeated safely.
const classMap = {
  table: 'table table-dark table-striped table-bordered'
}
const bindings = Object.keys(classMap)
  .map(key => ({
    type: 'output',
    regex: new RegExp(`<${key}(.*)>`, 'g'),
    replace: `<${key} class="${classMap[key]}" $1>`
}));
const options = {
  'tables': true,
  'simpleLineBreaks': true,
  'extensions': [...bindings]
};
const converter = new showdown.Converter(options);

function getMarkdown(text, outDiv) {
  target = document.getElementById(outDiv);
  html = converter.makeHtml(text);
  target.innerHTML = html;
}
