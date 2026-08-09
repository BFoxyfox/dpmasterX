#!/bin/sh

failed=0
export PERL5LIB="${PERL5LIB:+$PERL5LIB:}."

for i in test-*.pl ; do
	echo '-' $i
	./$i "$@" || failed=1
done

echo '- test-persistence.py'
./test-persistence.py || failed=1

exit $failed
