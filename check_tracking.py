import argparse
import bz2
import time

import mwapi
import mwparserfromhell
import mwxml

def standardize_template_names(t):
    """Standardize names of templates for matching purposes."""
    return t.strip().lower().replace(" ", "_")

def build_template_list(lang, main):
    """Build list of templates names (including redirects) based on the main template name."""
    session = mwapi.Session('https://{0}.wikipedia.org'.format(lang), user_agent="Reuse analysis - isaac@wikimedia.org")
    params = {'action':'query', 'prop':'redirects', 'titles':main, 'rdnamespace':10, 'rdlimit':500, 'format':'json', 'formatversion':2}
    result = session.get(params)
    template_names = set()
    for t in [result['query']['pages'][0]] + result['query']['pages'][0].get('redirects', []):
        name_start = t['title'].find(':') + 1
        template_names.add(standardize_template_names(t['title'][name_start:]))
    return template_names

def coord(template):
    # Coord -- if anything that looks like lat/lon, it's tracking
    for p in template.params:
        try:
            float(str(p.value))
            return True
        except ValueError:
            if 'latitude=' in p.name or 'dd=' in p.name:
                return True
    return False

def ac(template):
    # Authority Control -- only caveat is QID can be manually identified via QID=
    if template.params:
        if len(template.params) > 1 or not standardize_template_param_name(template.params[0]) == 'qid':
            return True
    return False

def tb(template):
    # Taxonbar -- only caveat is that QID can be manually identified via from=
    if template.params:
        if len(template.params) > 1 or not standardize_template_param_name(template.params[0]) == 'from':
            return True
    return False

def bda(template):
    # birth date templates are all always tracking
    return True

def el(template):
    # External links -- name is only common parameter that wouldn't override Wikidata call
    if template.params:
        if len(template.params) == 1 and standardize_template_param_name(template.params[0]) == 'name':
            return False
        return True
    return False

def standardize_template_param_name(param):
    """Standardize names of template parameters for matching."""
    return param.name.strip().lower()

# Category:Infobox_templates_using_Wikidata
# Category:External_link_templates_using_Wikidata
# Category:Templates_tracking_Wikidata
# Category:Templates_using_data_from_Wikidata
def get_templates_in_category(lang, category_name):
    """Get all templates in a category."""
    session = mwapi.Session('https://{0}.wikipedia.org'.format(lang), user_agent="Reuse analysis - isaac@wikimedia.org")
    params = {'action': 'query', 'list': 'categorymembers', 'cmtitle': category_name, 'cmnamespace': 10, 'cmlimit': 500,
              'format': 'json', 'formatversion': 2, 'cmprop':'title'}
    result = session.get(params)
    template_names = []
    for t in result['query']['categorymembers']:
        template_names.append(t['title'])
    return template_names

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lang", default="en", help="Wikipedia language to analyze")
    parser.add_argument('--output_tsv', default='output.tsv', help='Output data TSV.')
    args = parser.parse_args()

    # Look for coordinate, authority control, taxonbar, birth date, and external link templates
    main_templates = {'cd': {'en': ['Template:Coord'], 'func': coord},
                      'ac': {'en': ['Template:Authority control'], 'func': ac},
                      'tb': {'en': ['Template:Taxonbar'], 'func': tb},
                      'bd': {
                          'en': ['Template:Birth_date', 'Template:Birth_date_and_age', 'Template:Birth_year_and_age',
                                 'Template:Birth-date', 'Template:Birth-date_and_age'], 'func': bda},
                      'el': {'en': get_templates_in_category('en', 'Category:External_link_templates_using_Wikidata'),
                             'func': el}
                      }

    # current full wikitext of enwiki articles
    dump_fn = '/mnt/data/xmldatadumps/public/{0}wiki/latest/{0}wiki-latest-pages-articles.xml.bz2'.format(args.lang)
    dump = mwxml.Dump.from_file(bz2.open(dump_fn))
    wikidata_usage = {}
    template_names = {}
    for t in main_templates:
        wikidata_usage[t] = {'tracking':0, 'transclusion':0}
        template_names[t] = set()
        for t_name in main_templates[t][args.lang]:
            template_names[t].update(build_template_list(args.lang, t_name))
            time.sleep(0.5)
    for t in template_names:
        print("{0}: {1} templates names".format(t, len(template_names[t])))
    evaluated = 0
    prob_wbc = 0
    actual_wbc = 0
    # loop through dump and for every article, gather templates and check whether any match the ones I'm tracking
    # if match, check whether usage looks like transclusion or tracking only
    for i, page in enumerate(dump, start=1):
        if page.namespace == 0 and not page.redirect:
            evaluated += 1
            for revision in page:
                wikitext = mwparserfromhell.parse(revision.text)
                templates = wikitext.filter_templates()
                wbc = False
                tracking_only = True
                for t in templates:
                    t_name = standardize_template_names(t.name)
                    for t_check in template_names:
                        if t_name in template_names[t_check]:
                            if main_templates[t_check]['func'](t):
                                wikidata_usage[t_check]['tracking'] += 1
                                wbc = True
                            else:
                                wikidata_usage[t_check]['transclusion'] += 1
                                wbc = True
                                tracking_only = False
                if wbc:
                    prob_wbc += 1
                    if not tracking_only:
                        actual_wbc += 1
        if i % 10000 == 0:
            status = []
            for w in wikidata_usage:
                n = sum(wikidata_usage[w].values())
                if n:
                    status.append('{0} (n={1}):\t{2:.1f}% transclusion'.format(w, n, wikidata_usage[w]['transclusion'] * 100 / n))
                else:
                    status.append('{0} (n={1}): --'.format(w, n))
            print("{0} pages processed. {1} evaluated. {2} ({3:.1f}%) likely on wbc_entity_usage. {4} ({5:.1f}%) legitimately on wbc_entity_usage. Status:\n{6}".format(
                i, evaluated, prob_wbc, prob_wbc * 100 / evaluated, actual_wbc, 100 * actual_wbc / evaluated, '\n'.join(status)))
    status = []
    with open(args.output_tsv, 'w') as fout:
        fout.write('type\ttransclusion\ttracking\n')
        fout.write('total:{0}\t\t\n'.format(evaluated))
        fout.write('wbc_estimate\t{0}\t{1}\n'.format(actual_wbc, prob_wbc - actual_wbc))
        for w in wikidata_usage:
            n = sum(wikidata_usage[w].values())
            if n:
                status.append(
                    '{0} (n={1}):\t{2:.1f}% transclusion'.format(w, n, wikidata_usage[w]['transclusion'] * 100 / n))
            else:
                status.append('{0} (n={1}): --'.format(w, n))
            fout.write('{0}\t{1}\t{2}\n'.format(w, wikidata_usage[w]['transclusion'], wikidata_usage[w]['tracking']))
        print(
            "{0} pages processed. {1} evaluated. {2} ({3:.1f}%) likely on wbc_entity_usage. {4} ({5:.1f}%) legitimately on wbc_entity_usage. Status:\n{6}".format(
                i, evaluated, prob_wbc, prob_wbc * 100 / evaluated, actual_wbc, 100 * actual_wbc / evaluated,
                '\n'.join(status)))


if __name__ == "__main__":
    main()